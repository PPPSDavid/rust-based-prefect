from __future__ import annotations

import contextvars
import inspect
import sys
import textwrap
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import wraps
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast, overload
from collections.abc import Callable, Iterable, Sequence
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from .gates import GateFuture
    from .subflows import SubflowFuture

from .hooks import (
    TransitionContext,
    TransitionHookSpec,
    compile_transition_hooks,
    dispatch_transition_hooks,
)
from .runtime import InMemoryControlPlane, RunState, SetStateResult, TaskRunRecord
from .task_runners import (
    MapTaskRunner,
    ProcessPoolTaskRunner,
    ThreadPoolTaskRunner,
    default_task_runner_from_env,
)

# Reused when ``transition_hooks`` is set (avoids per-submit list literals on the hot path).
_TASK_HOOK_START_EDGES: tuple[
    tuple[RunState, RunState, str, dict[str, Any] | None], ...
] = (
    (RunState.SCHEDULED, RunState.PENDING, "task_pending", None),
    (RunState.PENDING, RunState.RUNNING, "task_running", None),
)

# Static planner output keyed by flow callable identity (source is stable per decorated function).
_FORECAST_BY_FLOW_FN: dict[int, dict[str, Any]] = {}

T = TypeVar("T")

_CONTROL_PLANE = InMemoryControlPlane()

_ACTIVE_TASK_RUNNER: contextvars.ContextVar[
    MapTaskRunner | ProcessPoolTaskRunner | None
] = contextvars.ContextVar("ironflow_active_task_runner", default=None)
_ACTIVE_FLOW_RUN: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "ironflow_active_flow_run", default=None
)
_ACTIVE_DEPLOYMENT_RUN: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "ironflow_active_deployment_run", default=None
)


@dataclass
class TaskFuture(Generic[T]):
    value: T
    task_run_id: str | None = None
    planned_node_id: str | None = None

    def result(self) -> T:
        return self.value


def wait(futures: Sequence[TaskFuture[Any] | SubflowFuture[Any] | GateFuture[Any]]) -> list[Any]:
    return [future.result() for future in futures]


def set_control_plane(control_plane: InMemoryControlPlane) -> None:
    global _CONTROL_PLANE
    _CONTROL_PLANE = control_plane


class TaskWrapper:
    def __init__(
        self,
        fn: Callable[..., T],
        name: str | None = None,
        *,
        transition_hooks: tuple[TransitionHookSpec, ...] | None = None,
        tags: tuple[str, ...] = (),
        persist_result: bool = False,
    ) -> None:
        self.fn = fn
        self.name = name or getattr(fn, "__name__", "<task>")
        self._transition_hooks = transition_hooks
        self.tags = tags
        self.persist_result = bool(persist_result)
        wraps(fn)(self)

    def __call__(self, *args: Any, **kwargs: Any) -> T:
        resolved_args = [_resolve(v) for v in args]
        resolved_kwargs = {k: _resolve(v) for k, v in kwargs.items()}
        return cast(T, self.fn(*resolved_args, **resolved_kwargs))

    def _start_task_run(
        self, flow_run_id: UUID, planned_node_id: str | None
    ) -> tuple[TaskRunRecord, list[str]]:
        """Create task run, PENDING, acquire tag slots, then RUNNING.

        Untagged tasks keep the batched PENDING+RUNNING start for performance.
        """
        task_run = _CONTROL_PLANE.create_task_run(
            flow_run_id,
            self.name,
            planned_node_id=planned_node_id,
            tags=self.tags,
        )
        lease_ids: list[str] = []
        if self.tags:
            _CONTROL_PLANE.record_task_event(task_run.task_run_id, "task_pending", None)
            th = self._transition_hooks
            if th:
                _emit_task_single_hook_edge(
                    th,
                    task_run,
                    self.name,
                    RunState.SCHEDULED,
                    RunState.PENDING,
                    "task_pending",
                    None,
                )
            from .concurrency import (
                ConcurrencyLimitError,
                acquire_tag_slots_for_task,
            )

            try:
                lease_ids = acquire_tag_slots_for_task(
                    self.tags,
                    task_run_id=str(task_run.task_run_id),
                    plane=_CONTROL_PLANE,
                )
            except ConcurrencyLimitError:
                _CONTROL_PLANE.record_task_event(
                    task_run.task_run_id,
                    "task_cancelled",
                    {
                        "task_name": self.name,
                        "error": "tag concurrency limit denied (limit=0)",
                    },
                )
                if th:
                    _emit_task_single_hook_edge(
                        th,
                        task_run,
                        self.name,
                        RunState.PENDING,
                        RunState.CANCELLED,
                        "task_cancelled",
                        {"task_name": self.name},
                    )
                raise
            _CONTROL_PLANE.record_task_event(task_run.task_run_id, "task_running", None)
            if th:
                _emit_task_single_hook_edge(
                    th,
                    task_run,
                    self.name,
                    RunState.PENDING,
                    RunState.RUNNING,
                    "task_running",
                    None,
                )
        else:
            _CONTROL_PLANE.record_task_events_batch(
                task_run.task_run_id,
                [
                    ("task_pending", None),
                    ("task_running", None),
                ],
            )
            th = self._transition_hooks
            if th:
                _emit_task_transition_edges(
                    th, task_run, self.name, _TASK_HOOK_START_EDGES
                )
        return task_run, lease_ids

    def _release_tag_leases(self, lease_ids: list[str]) -> None:
        if lease_ids:
            _CONTROL_PLANE.release_concurrency_slots(lease_ids)

    def submit(
        self,
        *args: Any,
        wait_for: Sequence[TaskFuture[Any] | SubflowFuture[Any] | GateFuture[Any]] | None = None,
        **kwargs: Any,
    ) -> TaskFuture[T]:
        if wait_for:
            wait(wait_for)

        flow_run_id = _ACTIVE_FLOW_RUN.get()
        task_run = None
        lease_ids: list[str] = []
        if flow_run_id is not None:
            planned_node_id = _CONTROL_PLANE.next_planned_node_id(
                flow_run_id, self.name
            )
            hit, cached = _CONTROL_PLANE.lookup_resumed_task_result(
                flow_run_id,
                planned_node_id,
                persist_result=self.persist_result,
            )
            if hit:
                # Resume skip does not consume tag concurrency slots.
                return self._complete_from_cache(
                    flow_run_id, planned_node_id, cast(T, cached)
                )
            task_run, lease_ids = self._start_task_run(flow_run_id, planned_node_id)

        try:
            result = self(*args, **kwargs)
            if task_run is not None:
                self._finalize_completed_task(task_run, result, cache_hit=False)
            return TaskFuture(
                result,
                task_run_id=str(task_run.task_run_id) if task_run is not None else None,
                planned_node_id=task_run.planned_node_id
                if task_run is not None
                else None,
            )
        except Exception as exc:
            from .cancellation import FlowRunCancelled

            if isinstance(exc, FlowRunCancelled):
                raise
            if task_run is not None:
                # If ``task_completed`` progressed the FSM but persistence raised, do not emit FAILED.
                try:
                    st = _CONTROL_PLANE.get_task_run(task_run.task_run_id).state
                except Exception:
                    st = None
                if st in (RunState.RUNNING, RunState.PENDING):
                    _CONTROL_PLANE.record_task_event(
                        task_run.task_run_id,
                        "task_failed",
                        {"task_name": self.name, "error": str(exc)},
                    )
                    th = self._transition_hooks
                    if th and st == RunState.RUNNING:
                        _emit_task_single_hook_edge(
                            th,
                            task_run,
                            self.name,
                            RunState.RUNNING,
                            RunState.FAILED,
                            "task_failed",
                            {"task_name": self.name, "error": str(exc)},
                        )
            raise
        finally:
            self._release_tag_leases(lease_ids)

    def _complete_from_cache(
        self,
        flow_run_id: UUID,
        planned_node_id: str | None,
        value: T,
    ) -> TaskFuture[T]:
        task_run = _CONTROL_PLANE.create_task_run(
            flow_run_id,
            self.name,
            planned_node_id=planned_node_id,
            tags=self.tags,
        )
        _CONTROL_PLANE.record_task_events_batch(
            task_run.task_run_id,
            [
                ("task_pending", None),
                ("task_running", None),
            ],
        )
        th = self._transition_hooks
        if th:
            _emit_task_transition_edges(
                th, task_run, self.name, _TASK_HOOK_START_EDGES
            )
        self._finalize_completed_task(task_run, value, cache_hit=True)
        return TaskFuture(
            value,
            task_run_id=str(task_run.task_run_id),
            planned_node_id=task_run.planned_node_id,
        )

    def _finalize_completed_task(
        self,
        task_run: TaskRunRecord,
        result: Any,
        *,
        cache_hit: bool,
        map_index: int | None = None,
    ) -> None:
        summary_extra = _CONTROL_PLANE.store_task_result_for_resume(
            task_run.flow_run_id,
            task_run.task_run_id,
            self.name,
            task_run.planned_node_id,
            result,
            persist_result=self.persist_result,
            map_index=map_index,
        )
        data: dict[str, Any] = {"task_name": self.name, **summary_extra}
        if cache_hit:
            data["cache_hit"] = True
        _CONTROL_PLANE.record_task_event(task_run.task_run_id, "task_completed", data)
        th = self._transition_hooks
        if th:
            _emit_task_single_hook_edge(
                th,
                task_run,
                self.name,
                RunState.RUNNING,
                RunState.COMPLETED,
                "task_completed",
                data,
            )

    def map(
        self,
        values: Iterable[Any],
        wait_for: Sequence[TaskFuture[Any] | SubflowFuture[Any] | GateFuture[Any]] | None = None,
    ) -> list[TaskFuture[T]]:
        runner = _ACTIVE_TASK_RUNNER.get()
        if runner is None:
            runner = default_task_runner_from_env()
        vals = list(values)
        wf: list[TaskFuture[Any] | SubflowFuture[Any] | GateFuture[Any]] | None = (
            list(wait_for) if wait_for else None
        )
        if isinstance(runner, ProcessPoolTaskRunner):
            return self._map_process_pool(vals, wf, runner)
        if (
            isinstance(runner, ThreadPoolTaskRunner)
            and len(vals) > 1
            and runner.resolve_max_workers() > 1
        ):
            return self._map_thread_pool(vals, wf, runner)
        assert isinstance(runner, MapTaskRunner)
        return runner.map_values(
            self,
            vals,
            wf,
            wait,
            lambda v: self.submit(v, wait_for=None),
        )

    def _prepare_map_task_runs(
        self, vals: list[Any]
    ) -> list[tuple[TaskRunRecord | None, Any]]:
        flow_run_id = _ACTIVE_FLOW_RUN.get()
        metas: list[tuple[TaskRunRecord | None, Any]] = []
        planned_node_id: str | None = None
        for v in vals:
            task_run = None
            if flow_run_id is not None:
                if planned_node_id is None:
                    planned_node_id = _CONTROL_PLANE.next_planned_node_id(
                        flow_run_id, self.name
                    )
                task_run = _CONTROL_PLANE.create_task_run(
                    flow_run_id,
                    self.name,
                    planned_node_id=planned_node_id,
                    tags=self.tags,
                )
                if self.tags:
                    _CONTROL_PLANE.record_task_event(
                        task_run.task_run_id, "task_pending", None
                    )
                    th = self._transition_hooks
                    if th:
                        _emit_task_single_hook_edge(
                            th,
                            task_run,
                            self.name,
                            RunState.SCHEDULED,
                            RunState.PENDING,
                            "task_pending",
                            None,
                        )
                else:
                    _CONTROL_PLANE.record_task_events_batch(
                        task_run.task_run_id,
                        [
                            ("task_pending", None),
                            ("task_running", None),
                        ],
                    )
                    th = self._transition_hooks
                    if th:
                        _emit_task_transition_edges(
                            th, task_run, self.name, _TASK_HOOK_START_EDGES
                        )
            metas.append((task_run, v))
        return metas

    def _finalize_map_task_runs(
        self, metas: list[tuple[TaskRunRecord | None, Any]], outs: list[Any]
    ) -> list[TaskFuture[T]]:
        out: list[TaskFuture[T]] = []
        for index, ((task_run, _v), raw) in enumerate(zip(metas, outs, strict=True)):
            if task_run is not None:
                self._finalize_completed_task(
                    task_run, raw, cache_hit=False, map_index=index
                )
            out.append(
                TaskFuture(
                    raw,
                    task_run_id=str(task_run.task_run_id)
                    if task_run is not None
                    else None,
                    planned_node_id=task_run.planned_node_id
                    if task_run is not None
                    else None,
                )
            )
        return out

    def _fail_map_task_runs(
        self, metas: list[tuple[TaskRunRecord | None, Any]], exc: Exception
    ) -> None:
        for task_run, _v in metas:
            if task_run is None:
                continue
            try:
                st = _CONTROL_PLANE.get_task_run(task_run.task_run_id).state
            except Exception:
                st = None
            if st not in (RunState.RUNNING, RunState.PENDING):
                continue
            _CONTROL_PLANE.record_task_event(
                task_run.task_run_id,
                "task_failed",
                {"task_name": self.name, "error": str(exc)},
            )
            th = self._transition_hooks
            if th and st == RunState.RUNNING:
                _emit_task_single_hook_edge(
                    th,
                    task_run,
                    self.name,
                    RunState.RUNNING,
                    RunState.FAILED,
                    "task_failed",
                    {"task_name": self.name, "error": str(exc)},
                )

    def _run_tagged_map_body(self, task_run: TaskRunRecord | None, value: Any) -> Any:
        """Acquire tag slots, enter RUNNING, execute body, release slots."""
        lease_ids: list[str] = []
        if task_run is not None and self.tags:
            from .concurrency import (
                ConcurrencyLimitError,
                acquire_tag_slots_for_task,
            )

            try:
                lease_ids = acquire_tag_slots_for_task(
                    self.tags,
                    task_run_id=str(task_run.task_run_id),
                    plane=_CONTROL_PLANE,
                )
            except ConcurrencyLimitError:
                _CONTROL_PLANE.record_task_event(
                    task_run.task_run_id,
                    "task_cancelled",
                    {
                        "task_name": self.name,
                        "error": "tag concurrency limit denied (limit=0)",
                    },
                )
                raise
            _CONTROL_PLANE.record_task_event(task_run.task_run_id, "task_running", None)
            th = self._transition_hooks
            if th:
                _emit_task_single_hook_edge(
                    th,
                    task_run,
                    self.name,
                    RunState.PENDING,
                    RunState.RUNNING,
                    "task_running",
                    None,
                )
        try:
            return self.fn(value)
        finally:
            self._release_tag_leases(lease_ids)

    def _map_thread_pool(
        self,
        vals: list[Any],
        wait_for: list[TaskFuture[Any] | SubflowFuture[Any] | GateFuture[Any]] | None,
        runner: ThreadPoolTaskRunner,
    ) -> list[TaskFuture[T]]:
        """Map with task bodies in a thread pool; control-plane work stays on the caller thread."""
        if wait_for:
            wait(wait_for)
        if not vals:
            return []
        metas = self._prepare_map_task_runs(vals)
        mx = min(len(vals), runner.resolve_max_workers())
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=mx) as pool:
            try:
                if self.tags:
                    outs = list(
                        pool.map(
                            lambda m: self._run_tagged_map_body(m[0], m[1]),
                            metas,
                        )
                    )
                else:
                    outs = list(pool.map(self.fn, [m[1] for m in metas]))
            except Exception as exc:
                self._fail_map_task_runs(metas, exc)
                raise
        return self._finalize_map_task_runs(metas, outs)

    def _map_process_pool(
        self,
        vals: list[Any],
        wait_for: list[TaskFuture[Any] | SubflowFuture[Any] | GateFuture[Any]] | None,
        runner: ProcessPoolTaskRunner,
    ) -> list[TaskFuture[T]]:
        """Map via child processes; task body must be picklable (single positional arg per value)."""
        if wait_for:
            wait(wait_for)
        if not vals:
            return []
        metas = self._prepare_map_task_runs(vals)
        mx = min(len(vals), runner.resolve_max_workers())
        fn = self.fn
        if self.tags:
            # Tag slots on the parent; bodies run in children (process pool cannot share GCL).
            # Outer threads acquire/release while ProcessPoolExecutor runs picklable bodies.
            from concurrent.futures import ThreadPoolExecutor

            with ProcessPoolExecutor(max_workers=mx) as pool:

                def _one(item: tuple[TaskRunRecord | None, Any]) -> Any:
                    task_run, v = item
                    lease_ids: list[str] = []
                    if task_run is not None:
                        from .concurrency import (
                            ConcurrencyLimitError,
                            acquire_tag_slots_for_task,
                        )

                        try:
                            lease_ids = acquire_tag_slots_for_task(
                                self.tags,
                                task_run_id=str(task_run.task_run_id),
                                plane=_CONTROL_PLANE,
                            )
                        except ConcurrencyLimitError:
                            _CONTROL_PLANE.record_task_event(
                                task_run.task_run_id,
                                "task_cancelled",
                                {
                                    "task_name": self.name,
                                    "error": "tag concurrency limit denied (limit=0)",
                                },
                            )
                            raise
                        _CONTROL_PLANE.record_task_event(
                            task_run.task_run_id, "task_running", None
                        )
                        th = self._transition_hooks
                        if th:
                            _emit_task_single_hook_edge(
                                th,
                                task_run,
                                self.name,
                                RunState.PENDING,
                                RunState.RUNNING,
                                "task_running",
                                None,
                            )
                    try:
                        return pool.submit(fn, v).result()
                    finally:
                        self._release_tag_leases(lease_ids)

                with ThreadPoolExecutor(max_workers=mx) as orchestrator:
                    try:
                        outs = list(orchestrator.map(_one, metas))
                    except Exception as exc:
                        self._fail_map_task_runs(metas, exc)
                        raise
            return self._finalize_map_task_runs(metas, outs)

        with ProcessPoolExecutor(max_workers=mx) as pool:
            try:
                outs = list(pool.map(fn, [m[1] for m in metas]))
            except Exception as exc:
                self._fail_map_task_runs(metas, exc)
                raise
        return self._finalize_map_task_runs(metas, outs)


@overload
def task(
    fn: Callable[..., T],
    *,
    name: str | None = None,
    transition_hooks: Sequence[TransitionHookSpec] | None = None,
    tags: Sequence[str] | None = None,
    persist_result: bool = False,
) -> TaskWrapper: ...


@overload
def task(
    fn: None = None,
    *,
    name: str | None = None,
    transition_hooks: Sequence[TransitionHookSpec] | None = None,
    tags: Sequence[str] | None = None,
    persist_result: bool = False,
) -> Callable[[Callable[..., T]], TaskWrapper]: ...


def task(
    fn: Callable[..., T] | None = None,
    *,
    name: str | None = None,
    transition_hooks: Sequence[TransitionHookSpec] | None = None,
    tags: Sequence[str] | None = None,
    persist_result: bool = False,
) -> TaskWrapper | Callable[[Callable[..., T]], TaskWrapper]:
    def decorate(f: Callable[..., T]) -> TaskWrapper:
        compiled = compile_transition_hooks(transition_hooks)
        tag_tuple = tuple(str(t) for t in (tags or ()))
        return TaskWrapper(
            f,
            name=name,
            transition_hooks=compiled,
            tags=tag_tuple,
            persist_result=persist_result,
        )

    if fn is None:
        return decorate
    return decorate(fn)


def flow(
    fn: Callable[..., T] | None = None,
    *,
    name: str | None = None,
    task_runner: MapTaskRunner | ProcessPoolTaskRunner | None = None,
    transition_hooks: Sequence[TransitionHookSpec] | None = None,
) -> Callable[..., Any]:
    def decorate(f: Callable[..., T]) -> Callable[..., T]:
        flow_name = name or getattr(f, "__name__", "<flow>")
        resolved_runner = (
            task_runner if task_runner is not None else default_task_runner_from_env()
        )
        compiled_flow_hooks = compile_transition_hooks(transition_hooks)

        @wraps(f)
        def wrapped(*args: Any, **kwargs: Any) -> T:
            from .cancellation import FlowRunCancelled

            token = _ACTIVE_TASK_RUNNER.set(resolved_runner)
            parent_active_flow_run = _ACTIVE_FLOW_RUN.get()
            flow_token = _ACTIVE_FLOW_RUN.set(None)
            fh = compiled_flow_hooks
            try:
                dep_run_id = _ACTIVE_DEPLOYMENT_RUN.get()
                parent_flow_run_id = None
                parent_task_run_id = None
                execution_mode = None
                resume_from: UUID | None = None
                if dep_run_id is not None:
                    dep_run = _CONTROL_PLANE.get_deployment_run(dep_run_id)
                    if dep_run:
                        if dep_run.get("parent_flow_run_id"):
                            parent_flow_run_id = UUID(
                                str(dep_run["parent_flow_run_id"])
                            )
                        if dep_run.get("parent_task_run_id"):
                            parent_task_run_id = UUID(
                                str(dep_run["parent_task_run_id"])
                            )
                        if dep_run.get("resume_from_flow_run_id"):
                            resume_from = UUID(str(dep_run["resume_from_flow_run_id"]))
                        execution_mode = "deployment"
                elif parent_active_flow_run is not None:
                    parent_flow_run_id = parent_active_flow_run
                    execution_mode = "inline"
                if resume_from is None:
                    resume_from = _CONTROL_PLANE.consume_pending_resume()
                record = _CONTROL_PLANE.create_flow_run(
                    flow_name,
                    parent_flow_run_id=parent_flow_run_id,
                    parent_task_run_id=parent_task_run_id,
                    execution_mode=execution_mode,
                    resume_from_flow_run_id=resume_from,
                )
                _ACTIVE_FLOW_RUN.set(record.run_id)
                if dep_run_id is not None:
                    _CONTROL_PLANE.attach_flow_run_to_deployment_run(
                        dep_run_id, record.run_id
                    )
                manifest_info = _compile_forecast_for_flow(f, flow_name)
                _CONTROL_PLANE.save_flow_manifest(
                    run_id=record.run_id,
                    manifest=manifest_info["manifest"],
                    forecast=manifest_info["forecast"],
                    warnings=manifest_info["warnings"],
                    fallback_required=manifest_info["fallback_required"],
                    source=manifest_info["source"],
                )
                start_transitions: list[tuple[RunState, UUID, str, int | None]] = [
                    (RunState.PENDING, uuid4(), "propose", 0),
                    (RunState.RUNNING, uuid4(), "start", 1),
                ]
                batch_results = _CONTROL_PLANE.set_flow_states_batch(
                    record.run_id, start_transitions
                )
                if fh:
                    _emit_flow_hooks_for_batch(
                        fh,
                        record.run_id,
                        RunState.SCHEDULED,
                        start_transitions,
                        batch_results,
                    )
                try:
                    result = f(*args, **kwargs)
                    current = _CONTROL_PLANE.get_flow(record.run_id)
                    if current.state == RunState.CANCELLED:
                        raise FlowRunCancelled(
                            f"flow run {record.run_id} was cancelled"
                        )
                    prev = current.state
                    done = _CONTROL_PLANE.set_flow_state(
                        record.run_id,
                        RunState.COMPLETED,
                        uuid4(),
                        "complete",
                        expected_version=current.version,
                    )
                    if fh and done.status == "applied":
                        _emit_flow_transition(
                            fh, record.run_id, prev, RunState.COMPLETED, "complete"
                        )
                    _CONTROL_PLANE.set_flow_result(record.run_id, result)
                    return result
                except FlowRunCancelled:
                    if (
                        _CONTROL_PLANE.get_flow(record.run_id).state
                        != RunState.CANCELLED
                    ):
                        prev = _CONTROL_PLANE.get_flow(record.run_id).state
                        cancelled = _CONTROL_PLANE.set_flow_state(
                            record.run_id,
                            RunState.CANCELLED,
                            uuid4(),
                            "cancel",
                            expected_version=2,
                        )
                        if fh and cancelled.status == "applied":
                            _emit_flow_transition(
                                fh, record.run_id, prev, RunState.CANCELLED, "cancel"
                            )
                    raise
                except Exception:
                    if (
                        _CONTROL_PLANE.get_flow(record.run_id).state
                        == RunState.CANCELLED
                    ):
                        raise
                    prev = _CONTROL_PLANE.get_flow(record.run_id).state
                    failed = _CONTROL_PLANE.set_flow_state(
                        record.run_id,
                        RunState.FAILED,
                        uuid4(),
                        "fail",
                        expected_version=2,
                    )
                    if fh and failed.status == "applied":
                        _emit_flow_transition(
                            fh, record.run_id, prev, RunState.FAILED, "fail"
                        )
                    raise
            finally:
                _ACTIVE_FLOW_RUN.reset(flow_token)
                _ACTIVE_TASK_RUNNER.reset(token)

        return wrapped

    if fn is None:
        return decorate
    return decorate(fn)


def _emit_flow_transition(
    specs: tuple[TransitionHookSpec, ...] | None,
    flow_run_id: UUID,
    from_state: RunState,
    to_state: RunState,
    transition_kind: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not specs:
        return
    ctx = TransitionContext(
        kind="flow",
        flow_run_id=flow_run_id,
        from_state=from_state,
        to_state=to_state,
        transition_kind=transition_kind,
        metadata=metadata,
    )
    dispatch_transition_hooks(specs, ctx)


def _emit_flow_hooks_for_batch(
    specs: tuple[TransitionHookSpec, ...] | None,
    flow_run_id: UUID,
    initial_from: RunState,
    transitions: list[tuple[RunState, UUID, str, int | None]],
    results: list[SetStateResult],
) -> None:
    if not specs:
        return
    prev = initial_from
    for (to_state, _tok, kind, _exp), res in zip(transitions, results, strict=True):
        if res.status != "applied":
            prev = res.state
            continue
        _emit_flow_transition(specs, flow_run_id, prev, to_state, kind)
        prev = res.state


def _emit_task_transition_edges(
    specs: tuple[TransitionHookSpec, ...],
    task_run: TaskRunRecord,
    task_name: str,
    edges: tuple[tuple[RunState, RunState, str, dict[str, Any] | None], ...],
) -> None:
    for from_state, to_state, event_type, meta in edges:
        ctx = TransitionContext(
            kind="task",
            flow_run_id=task_run.flow_run_id,
            from_state=from_state,
            to_state=to_state,
            event_type=event_type,
            task_run_id=task_run.task_run_id,
            task_name=task_name,
            planned_node_id=task_run.planned_node_id,
            metadata=meta,
        )
        dispatch_transition_hooks(specs, ctx)


def _emit_task_single_hook_edge(
    specs: tuple[TransitionHookSpec, ...],
    task_run: TaskRunRecord,
    task_name: str,
    from_state: RunState,
    to_state: RunState,
    event_type: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    ctx = TransitionContext(
        kind="task",
        flow_run_id=task_run.flow_run_id,
        from_state=from_state,
        to_state=to_state,
        event_type=event_type,
        task_run_id=task_run.task_run_id,
        task_name=task_name,
        planned_node_id=task_run.planned_node_id,
        metadata=metadata,
    )
    dispatch_transition_hooks(specs, ctx)


def _resolve(value: Any) -> Any:
    if isinstance(value, TaskFuture):
        return value.result()
    from .gates import GateFuture
    from .subflows import SubflowFuture

    if isinstance(value, GateFuture):
        return value.result()
    if isinstance(value, SubflowFuture):
        return value.result()
    return value


def _compile_forecast_for_flow(
    flow_fn: Callable[..., Any], flow_name: str
) -> dict[str, Any]:
    cache_key = id(flow_fn)
    cached = _FORECAST_BY_FLOW_FN.get(cache_key)
    if cached is not None and cached.get("flow_name") == flow_name:
        return cached["info"]

    info = _compile_forecast_for_flow_uncached(flow_fn, flow_name)
    _FORECAST_BY_FLOW_FN[cache_key] = {"flow_name": flow_name, "info": info}
    return info


def _task_symbols_for_flow(flow_fn: Callable[..., Any]) -> dict[str, str]:
    """Map flow-local symbols to runtime task names (including @task(name=...))."""
    symbols: dict[str, str] = {}
    try:
        unwrapped = inspect.unwrap(flow_fn)
        module = inspect.getmodule(unwrapped)
        namespaces: list[Mapping[str, Any]] = []
        if module is not None:
            namespaces.append(vars(module))
        closure = inspect.getclosurevars(unwrapped)
        if closure.globals:
            namespaces.append(closure.globals)
        if closure.nonlocals:
            namespaces.append(closure.nonlocals)
        for namespace in namespaces:
            for key, value in namespace.items():
                if isinstance(value, TaskWrapper):
                    symbols[key] = value.name
    except Exception:
        return symbols
    return symbols


def _compile_forecast_for_flow_uncached(
    flow_fn: Callable[..., Any], flow_name: str
) -> dict[str, Any]:
    try:
        from static_planner import compile_and_forecast
    except Exception:
        planner_src = Path(__file__).resolve().parents[3] / "static-planner" / "src"
        if planner_src.exists() and str(planner_src) not in sys.path:
            sys.path.append(str(planner_src))
        try:
            from static_planner import compile_and_forecast
        except Exception:
            return {
                "manifest": {},
                "forecast": {},
                "warnings": [
                    "Static planner not available; runtime fallback DAG will be used."
                ],
                "fallback_required": True,
                "source": "runtime",
            }

    try:
        source = textwrap.dedent(inspect.getsource(flow_fn))
        task_names = _task_symbols_for_flow(flow_fn)
        result = compile_and_forecast(
            source, flow_name=flow_name, task_names=task_names
        )
        diagnostics = result.get("diagnostics", {})
        return {
            "manifest": result.get("manifest", {}),
            "forecast": result.get("forecast", {}),
            "warnings": diagnostics.get("warnings", []),
            "fallback_required": bool(diagnostics.get("fallback_required", False)),
            "source": "forecast",
        }
    except Exception as exc:
        return {
            "manifest": {},
            "forecast": {},
            "warnings": [f"Forecast compile failed: {exc}"],
            "fallback_required": True,
            "source": "runtime",
        }
