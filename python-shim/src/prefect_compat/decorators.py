from __future__ import annotations

import contextvars
import inspect
import sys
import textwrap
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from functools import wraps
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast, overload
from collections.abc import Callable, Iterable, Sequence
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from .gates import GateFuture
    from .subflows import SubflowFuture

from .cancellation import FlowRunCancelled
from .errors import FlowChildrenFailed
from .hooks import (
    TransitionContext,
    TransitionHookSpec,
    compile_transition_hooks,
    dispatch_transition_hooks,
)
from .runtime import (
    FlowRunRecord,
    InMemoryControlPlane,
    RunState,
    SetStateResult,
    TaskRunRecord,
)
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
# Shared pool for concurrent ``submit`` orchestration (thread + process runners).
_ACTIVE_SUBMIT_EXECUTOR: contextvars.ContextVar[ThreadPoolExecutor | None] = (
    contextvars.ContextVar("ironflow_active_submit_executor", default=None)
)
# Process pool for picklable task bodies under ``ProcessPoolTaskRunner`` submit.
_ACTIVE_PROCESS_POOL: contextvars.ContextVar[ProcessPoolExecutor | None] = (
    contextvars.ContextVar("ironflow_active_process_pool", default=None)
)

_UNSET: Any = object()


class TaskFuture(Generic[T]):
    """Future for a submitted task.

    Completed synchronously (sequential / map finalize) or via an underlying
    ``concurrent.futures.Future`` when a non-sequential task runner orchestrates
    wait/acquire/body off the coordinating thread.
    """

    __slots__ = ("task_run_id", "planned_node_id", "_value", "_cfuture")

    def __init__(
        self,
        value: Any = _UNSET,
        task_run_id: str | None = None,
        planned_node_id: str | None = None,
        *,
        _cfuture: Future[Any] | None = None,
    ) -> None:
        # Keep positional ``TaskFuture(result, ...)`` compatible with map finalize.
        self.task_run_id = task_run_id
        self.planned_node_id = planned_node_id
        self._value = value
        self._cfuture = _cfuture

    @property
    def value(self) -> T:
        return self.result()

    def result(self) -> T:
        if self._cfuture is not None:
            return cast(T, self._cfuture.result())
        if self._value is _UNSET:
            raise RuntimeError("TaskFuture has no result yet")
        return cast(T, self._value)

    def wait(self) -> None:
        self.result()


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
    ) -> None:
        self.fn = fn
        self.name = name or getattr(fn, "__name__", "<task>")
        self._transition_hooks = transition_hooks
        self.tags = tags
        wraps(fn)(self)

    def __call__(self, *args: Any, **kwargs: Any) -> T:
        resolved_args = [_resolve(v) for v in args]
        resolved_kwargs = {k: _resolve(v) for k, v in kwargs.items()}
        return cast(T, self.fn(*resolved_args, **resolved_kwargs))

    def _create_pending_task_run(
        self,
        flow_run_id: UUID,
        planned_node_id: str | None,
        *,
        contribute_to_flow_state: bool = True,
    ) -> TaskRunRecord:
        """Create a task run and record PENDING only (no tag acquire / RUNNING)."""
        task_run = _CONTROL_PLANE.create_task_run(
            flow_run_id,
            self.name,
            planned_node_id=planned_node_id,
            tags=self.tags,
            contribute_to_flow_state=contribute_to_flow_state,
        )
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
        return task_run

    def _promote_pending_to_running(self, task_run: TaskRunRecord) -> list[str]:
        """Acquire tag slots (if any), then transition PENDING → RUNNING."""
        lease_ids: list[str] = []
        th = self._transition_hooks
        if self.tags:
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
        return lease_ids

    def _start_task_run(
        self,
        flow_run_id: UUID,
        planned_node_id: str | None,
        *,
        contribute_to_flow_state: bool = True,
    ) -> tuple[TaskRunRecord, list[str]]:
        """Create task run through RUNNING on the coordinating thread (sequential path).

        Untagged tasks keep the batched PENDING+RUNNING start for performance.
        """
        if self.tags:
            task_run = self._create_pending_task_run(
                flow_run_id,
                planned_node_id,
                contribute_to_flow_state=contribute_to_flow_state,
            )
            return task_run, self._promote_pending_to_running(task_run)

        task_run = _CONTROL_PLANE.create_task_run(
            flow_run_id,
            self.name,
            planned_node_id=planned_node_id,
            tags=self.tags,
            contribute_to_flow_state=contribute_to_flow_state,
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
        return task_run, []

    def _release_tag_leases(self, lease_ids: list[str]) -> None:
        if lease_ids:
            _CONTROL_PLANE.release_concurrency_slots(lease_ids)

    def submit(
        self,
        *args: Any,
        wait_for: Sequence[TaskFuture[Any] | SubflowFuture[Any] | GateFuture[Any]] | None = None,
        detach: bool = False,
        **kwargs: Any,
    ) -> TaskFuture[T]:
        flow_run_id = _ACTIVE_FLOW_RUN.get()
        executor = _ACTIVE_SUBMIT_EXECUTOR.get()
        contribute = not detach

        # Non-sequential runners: create PENDING + return a future immediately.
        # wait_for, tag acquire, RUNNING, and the body run on a worker.
        if executor is not None:
            task_run = None
            if flow_run_id is not None:
                planned_node_id = _CONTROL_PLANE.next_planned_node_id(
                    flow_run_id, self.name
                )
                task_run = self._create_pending_task_run(
                    flow_run_id,
                    planned_node_id,
                    contribute_to_flow_state=contribute,
                )
            wait_for_list = list(wait_for) if wait_for else None
            ctx = contextvars.copy_context()
            cfuture = executor.submit(
                ctx.run,
                self._run_deferred_submit,
                args,
                kwargs,
                task_run,
                wait_for_list,
            )
            return TaskFuture(
                task_run_id=str(task_run.task_run_id) if task_run is not None else None,
                planned_node_id=task_run.planned_node_id if task_run is not None else None,
                _cfuture=cfuture,
            )

        # Sequential / no shared pool: gate deps, start RUNNING, then run body sync.
        if wait_for:
            wait(wait_for)
        task_run = None
        lease_ids: list[str] = []
        if flow_run_id is not None:
            planned_node_id = _CONTROL_PLANE.next_planned_node_id(
                flow_run_id, self.name
            )
            task_run, lease_ids = self._start_task_run(
                flow_run_id,
                planned_node_id,
                contribute_to_flow_state=contribute,
            )
        return self._run_submitted_body_sync(args, kwargs, task_run, lease_ids)

    def _run_deferred_submit(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        task_run: TaskRunRecord | None,
        wait_for_list: list[TaskFuture[Any] | SubflowFuture[Any] | GateFuture[Any]]
        | None,
    ) -> T:
        """Worker path: wait_for → tag acquire → RUNNING → body → finalize."""
        try:
            if wait_for_list:
                wait(wait_for_list)
            lease_ids: list[str] = []
            if task_run is not None:
                lease_ids = self._promote_pending_to_running(task_run)
            return self._execute_and_finalize_submit(args, kwargs, task_run, lease_ids)
        except FlowRunCancelled:
            raise
        except Exception as exc:
            # PENDING was created on the coordinating thread; if wait_for / promote
            # fails before RUNNING, close the run so it cannot linger forever.
            if task_run is not None:
                try:
                    st = _CONTROL_PLANE.get_task_run(task_run.task_run_id).state
                except Exception:
                    st = None
                if st == RunState.PENDING:
                    _CONTROL_PLANE.record_task_event(
                        task_run.task_run_id,
                        "task_cancelled",
                        {
                            "task_name": self.name,
                            "error": str(exc),
                            "reason": "dependency_or_start_failed",
                        },
                    )
                    th = self._transition_hooks
                    if th:
                        _emit_task_single_hook_edge(
                            th,
                            task_run,
                            self.name,
                            RunState.PENDING,
                            RunState.CANCELLED,
                            "task_cancelled",
                            {"task_name": self.name, "error": str(exc)},
                        )
            raise

    def _run_submitted_body_sync(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        task_run: TaskRunRecord | None,
        lease_ids: list[str] | None = None,
    ) -> TaskFuture[T]:
        result = self._execute_and_finalize_submit(
            args, kwargs, task_run, lease_ids or []
        )
        return TaskFuture(
            result,
            task_run_id=str(task_run.task_run_id) if task_run is not None else None,
            planned_node_id=task_run.planned_node_id if task_run is not None else None,
        )

    def _execute_and_finalize_submit(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        task_run: TaskRunRecord | None,
        lease_ids: list[str] | None = None,
    ) -> T:
        """Run the task body, then record COMPLETED/FAILED via the control plane.

        User work may run on a thread-pool worker (or a process-pool child); transitions
        always go through ``InMemoryControlPlane.record_task_event`` (Rust FSM + lock
        serialization), not a Python-only state write.
        """
        try:
            process_pool = _ACTIVE_PROCESS_POOL.get()
            if process_pool is not None:
                resolved_args = [_resolve(v) for v in args]
                resolved_kwargs = {k: _resolve(v) for k, v in kwargs.items()}
                result = cast(
                    T,
                    process_pool.submit(
                        self.fn, *resolved_args, **resolved_kwargs
                    ).result(),
                )
            else:
                result = self(*args, **kwargs)
            if task_run is not None:
                _CONTROL_PLANE.record_task_event(
                    task_run.task_run_id, "task_completed", {"task_name": self.name}
                )
                th = self._transition_hooks
                if th:
                    _emit_task_single_hook_edge(
                        th,
                        task_run,
                        self.name,
                        RunState.RUNNING,
                        RunState.COMPLETED,
                        "task_completed",
                        {"task_name": self.name},
                    )
            return result
        except Exception as exc:
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
            self._release_tag_leases(lease_ids or [])

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
        for (task_run, _v), raw in zip(metas, outs, strict=True):
            if task_run is not None:
                _CONTROL_PLANE.record_task_event(
                    task_run.task_run_id, "task_completed", {"task_name": self.name}
                )
                th = self._transition_hooks
                if th:
                    _emit_task_single_hook_edge(
                        th,
                        task_run,
                        self.name,
                        RunState.RUNNING,
                        RunState.COMPLETED,
                        "task_completed",
                        {"task_name": self.name},
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
) -> TaskWrapper: ...


@overload
def task(
    fn: None = None,
    *,
    name: str | None = None,
    transition_hooks: Sequence[TransitionHookSpec] | None = None,
    tags: Sequence[str] | None = None,
) -> Callable[[Callable[..., T]], TaskWrapper]: ...


def task(
    fn: Callable[..., T] | None = None,
    *,
    name: str | None = None,
    transition_hooks: Sequence[TransitionHookSpec] | None = None,
    tags: Sequence[str] | None = None,
) -> TaskWrapper | Callable[[Callable[..., T]], TaskWrapper]:
    def decorate(f: Callable[..., T]) -> TaskWrapper:
        compiled = compile_transition_hooks(transition_hooks)
        tag_tuple = tuple(str(t) for t in (tags or ()))
        return TaskWrapper(f, name=name, transition_hooks=compiled, tags=tag_tuple)

    if fn is None:
        return decorate
    return decorate(fn)


def flow(
    fn: Callable[..., T] | None = None,
    *,
    name: str | None = None,
    task_runner: MapTaskRunner | ProcessPoolTaskRunner | None = None,
    transition_hooks: Sequence[TransitionHookSpec] | None = None,
    final_state: str = "wait_all",
) -> Callable[..., Any]:
    def decorate(f: Callable[..., T]) -> Callable[..., T]:
        flow_name = name or getattr(f, "__name__", "<flow>")
        resolved_runner = (
            task_runner if task_runner is not None else default_task_runner_from_env()
        )
        compiled_flow_hooks = compile_transition_hooks(transition_hooks)
        completion_mode = str(final_state or "wait_all").strip().lower()
        if completion_mode not in {"wait_all", "explicit"}:
            raise ValueError(
                f"final_state must be 'wait_all' or 'explicit', got {final_state!r}"
            )

        @wraps(f)
        def wrapped(*args: Any, **kwargs: Any) -> T:
            token = _ACTIVE_TASK_RUNNER.set(resolved_runner)
            parent_active_flow_run = _ACTIVE_FLOW_RUN.get()
            flow_token = _ACTIVE_FLOW_RUN.set(None)
            fh = compiled_flow_hooks
            submit_executor: ThreadPoolExecutor | None = None
            submit_exec_token: contextvars.Token[ThreadPoolExecutor | None] | None = None
            process_pool: ProcessPoolExecutor | None = None
            process_pool_token: contextvars.Token[ProcessPoolExecutor | None] | None = (
                None
            )
            mx = (
                resolved_runner.resolve_max_workers()
                if isinstance(
                    resolved_runner, (ThreadPoolTaskRunner, ProcessPoolTaskRunner)
                )
                else 1
            )
            if isinstance(resolved_runner, ThreadPoolTaskRunner) and mx > 1:
                submit_executor = ThreadPoolExecutor(max_workers=mx)
                submit_exec_token = _ACTIVE_SUBMIT_EXECUTOR.set(submit_executor)
            elif isinstance(resolved_runner, ProcessPoolTaskRunner) and mx > 1:
                # Orchestrate wait_for / tag acquire / FSM on threads; bodies in processes.
                submit_executor = ThreadPoolExecutor(max_workers=mx)
                submit_exec_token = _ACTIVE_SUBMIT_EXECUTOR.set(submit_executor)
                process_pool = ProcessPoolExecutor(max_workers=mx)
                process_pool_token = _ACTIVE_PROCESS_POOL.set(process_pool)
            try:
                dep_run_id = _ACTIVE_DEPLOYMENT_RUN.get()
                parent_flow_run_id = None
                parent_task_run_id = None
                execution_mode = None
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
                        execution_mode = "deployment"
                elif parent_active_flow_run is not None:
                    parent_flow_run_id = parent_active_flow_run
                    execution_mode = "inline"
                record = _CONTROL_PLANE.create_flow_run(
                    flow_name,
                    parent_flow_run_id=parent_flow_run_id,
                    parent_task_run_id=parent_task_run_id,
                    execution_mode=execution_mode,
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
                    _drain_submit_pools(
                        submit_executor,
                        submit_exec_token,
                        process_pool,
                        process_pool_token,
                    )
                    submit_executor = None
                    submit_exec_token = None
                    process_pool = None
                    process_pool_token = None
                    current = _CONTROL_PLANE.get_flow(record.run_id)
                    if current.state == RunState.CANCELLED:
                        raise FlowRunCancelled(
                            f"flow run {record.run_id} was cancelled"
                        )
                    if completion_mode == "wait_all":
                        _finalize_wait_all(
                            record.run_id, result, fh, current
                        )
                    else:
                        _finalize_explicit(record.run_id, result, fh, current)
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
                except FlowChildrenFailed:
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
                _drain_submit_pools(
                    submit_executor,
                    submit_exec_token,
                    process_pool,
                    process_pool_token,
                )
                _ACTIVE_FLOW_RUN.reset(flow_token)
                _ACTIVE_TASK_RUNNER.reset(token)

        return wrapped

    if fn is None:
        return decorate
    return decorate(fn)


def _drain_submit_pools(
    executor: ThreadPoolExecutor | None,
    exec_token: contextvars.Token[ThreadPoolExecutor | None] | None,
    process_pool: ProcessPoolExecutor | None = None,
    process_token: contextvars.Token[ProcessPoolExecutor | None] | None = None,
) -> None:
    """Shut down per-flow submit orchestration / process pools after outstanding work."""
    if exec_token is not None:
        _ACTIVE_SUBMIT_EXECUTOR.reset(exec_token)
    if process_token is not None:
        _ACTIVE_PROCESS_POOL.reset(process_token)
    # Drain orchestration threads first so process-pool submits finish, then processes.
    if executor is not None:
        executor.shutdown(wait=True)
    if process_pool is not None:
        process_pool.shutdown(wait=True)


def _finalize_explicit(
    flow_run_id: UUID,
    result: Any,
    fh: tuple[TransitionHookSpec, ...] | None,
    current: FlowRunRecord,
) -> None:
    prev = current.state
    done = _CONTROL_PLANE.set_flow_state(
        flow_run_id,
        RunState.COMPLETED,
        uuid4(),
        "complete",
        expected_version=current.version,
    )
    if fh and done.status == "applied":
        _emit_flow_transition(
            fh, flow_run_id, prev, RunState.COMPLETED, "complete"
        )
    _CONTROL_PLANE.set_flow_result(flow_run_id, result)


def _finalize_wait_all(
    flow_run_id: UUID,
    result: Any,
    fh: tuple[TransitionHookSpec, ...] | None,
    current: FlowRunRecord,
) -> None:
    _CONTROL_PLANE.wait_contributing_children(flow_run_id)
    resolved = _CONTROL_PLANE.resolve_flow_terminal_state(flow_run_id)
    state_name = str(resolved.get("state", "FAILED"))
    kind = str(resolved.get("kind", "child_failed"))
    current = _CONTROL_PLANE.get_flow(flow_run_id)
    prev = current.state
    if prev == RunState.CANCELLED:
        raise FlowRunCancelled(f"flow run {flow_run_id} was cancelled")

    if state_name == "COMPLETED":
        done = _CONTROL_PLANE.set_flow_state(
            flow_run_id,
            RunState.COMPLETED,
            uuid4(),
            "complete",
            expected_version=current.version,
        )
        if fh and done.status == "applied":
            _emit_flow_transition(
                fh, flow_run_id, prev, RunState.COMPLETED, "complete"
            )
        _CONTROL_PLANE.set_flow_result(flow_run_id, result)
        return

    if state_name == "CANCELLED":
        cancelled = _CONTROL_PLANE.set_flow_state(
            flow_run_id,
            RunState.CANCELLED,
            uuid4(),
            "child_cancelled",
            expected_version=current.version,
        )
        if fh and cancelled.status == "applied":
            _emit_flow_transition(
                fh, flow_run_id, prev, RunState.CANCELLED, "child_cancelled"
            )
        raise FlowRunCancelled(
            f"flow run {flow_run_id} cancelled because a child was cancelled"
        )

    failed = _CONTROL_PLANE.set_flow_state(
        flow_run_id,
        RunState.FAILED,
        uuid4(),
        "child_failed",
        expected_version=current.version,
    )
    if fh and failed.status == "applied":
        _emit_flow_transition(
            fh, flow_run_id, prev, RunState.FAILED, "child_failed"
        )
    samples = resolved.get("sample_failures") or resolved.get("sample_incomplete") or []
    names = [
        str(s.get("task_name") or s.get("id"))
        for s in samples
        if isinstance(s, dict)
    ]
    detail = ", ".join(names[:5]) if names else kind
    raise FlowChildrenFailed(
        f"flow run {flow_run_id} failed: {kind} ({detail})",
        flow_run_id=str(flow_run_id),
        resolved_state=state_name,
        kind=kind,
        details=resolved,
    )


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
