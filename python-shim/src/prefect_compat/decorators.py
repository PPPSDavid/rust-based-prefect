from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from functools import wraps
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast, overload
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from .gates import GateFuture
    from .subflows import SubflowFuture

from .cancellation import FlowRunCancelled
from .context import bind_flow_metadata, bind_task_run, bound_flow_parameters
from .control_plane_registry import _require_control_plane, set_control_plane
from .errors import FlowChildrenFailed, TransitionRewriteFailed
from .forecast_compile import _compile_forecast_for_flow, clear_forecast_cache
from .graph_mode import (
    normalize_declared_graph_mode,
    resolve_graph_mode,
)
from .hooks import (
    TransitionHookSpec,
    compile_transition_hooks,
    emit_flow_hooks_for_batch,
    emit_flow_transition,
    emit_task_single_hook_edge,
    emit_task_transition_edges,
)
from .process_workers import ProcessWorkerTerminated, run_in_registered_process
from .result_codec import fingerprint_parameters, fingerprint_task_inputs
from .runtime import (
    FlowRunRecord,
    FlowRunSchedulingHeld,
    RunState,
    TaskRunRecord,
)
from .task_runners import (
    MapTaskRunner,
    ProcessPoolTaskRunner,
    ThreadPoolTaskRunner,
    default_task_runner_from_env,
)
from .transition_commit import (
    TaskTerminalOutcome,
    commit_flow_terminal,
    commit_task_terminal,
    raise_for_unsuccessful_task_terminal,
)

# Reused when ``transition_hooks`` is set (avoids per-submit list literals on the hot path).
_TASK_HOOK_START_EDGES: tuple[
    tuple[RunState, RunState, str, dict[str, Any] | None], ...
] = (
    (RunState.SCHEDULED, RunState.PENDING, "task_pending", None),
    (RunState.PENDING, RunState.RUNNING, "task_running", None),
)

T = TypeVar("T")

_ACTIVE_TASK_RUNNER: contextvars.ContextVar[
    MapTaskRunner | ProcessPoolTaskRunner | None
] = contextvars.ContextVar("ironflow_active_task_runner", default=None)
_ACTIVE_FLOW_RUN: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "ironflow_active_flow_run", default=None
)
_ACTIVE_DEPLOYMENT_RUN: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "ironflow_active_deployment_run", default=None
)
# Shared pool for concurrent ``submit`` under ``ThreadPoolTaskRunner`` (per flow invoke).
_ACTIVE_SUBMIT_EXECUTOR: contextvars.ContextVar[ThreadPoolExecutor | None] = (
    contextvars.ContextVar("ironflow_active_submit_executor", default=None)
)

_UNSET: Any = object()


class TaskFuture(Generic[T]):
    """Future for a submitted task.

    Completed synchronously (sequential / process / map finalize) or via an
    underlying ``concurrent.futures.Future`` when ``ThreadPoolTaskRunner`` runs
    the body off the coordinating thread.
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


def wait(
    futures: Sequence[TaskFuture[Any] | SubflowFuture[Any] | GateFuture[Any]],
) -> list[Any]:
    return [future.result() for future in futures]


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
        self,
        flow_run_id: UUID,
        planned_node_id: str | None,
        *,
        contribute_to_flow_state: bool = True,
    ) -> tuple[TaskRunRecord, list[str]]:
        """Create task run, PENDING, acquire tag slots, then RUNNING.

        Untagged tasks keep the batched PENDING+RUNNING start for performance.
        """
        task_run = _require_control_plane().create_task_run(
            flow_run_id,
            self.name,
            planned_node_id=planned_node_id,
            tags=self.tags,
            contribute_to_flow_state=contribute_to_flow_state,
        )
        lease_ids: list[str] = []
        if self.tags:
            _require_control_plane().record_task_event(
                task_run.task_run_id, "task_pending", None
            )
            th = self._transition_hooks
            if th:
                emit_task_single_hook_edge(
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
                    plane=_require_control_plane(),
                )
            except ConcurrencyLimitError:
                _require_control_plane().record_task_event(
                    task_run.task_run_id,
                    "task_cancelled",
                    {
                        "task_name": self.name,
                        "error": "tag concurrency limit denied (limit=0)",
                    },
                )
                if th:
                    emit_task_single_hook_edge(
                        th,
                        task_run,
                        self.name,
                        RunState.PENDING,
                        RunState.CANCELLED,
                        "task_cancelled",
                        {"task_name": self.name},
                    )
                raise
            _require_control_plane().record_task_event(
                task_run.task_run_id, "task_running", None
            )
            if th:
                emit_task_single_hook_edge(
                    th,
                    task_run,
                    self.name,
                    RunState.PENDING,
                    RunState.RUNNING,
                    "task_running",
                    None,
                )
        else:
            _require_control_plane().record_task_events_batch(
                task_run.task_run_id,
                [
                    ("task_pending", None),
                    ("task_running", None),
                ],
            )
            th = self._transition_hooks
            if th:
                emit_task_transition_edges(
                    th, task_run, self.name, _TASK_HOOK_START_EDGES
                )
        return task_run, lease_ids

    def _release_tag_leases(self, lease_ids: list[str]) -> None:
        if lease_ids:
            _require_control_plane().release_concurrency_slots(lease_ids)

    def submit(
        self,
        *args: Any,
        wait_for: Sequence[TaskFuture[Any] | SubflowFuture[Any] | GateFuture[Any]]
        | None = None,
        detach: bool = False,
        **kwargs: Any,
    ) -> TaskFuture[T]:
        # Gate deps on the coordinating thread so tag leases are not held while waiting.
        if wait_for:
            wait(wait_for)

        flow_run_id = _ACTIVE_FLOW_RUN.get()
        task_run = None
        lease_ids: list[str] = []
        input_fp = _fingerprint_resolved_inputs(args, kwargs)
        if flow_run_id is not None:
            planned_node_id = _require_control_plane().next_planned_node_id(
                flow_run_id, self.name
            )
            hit, cached = _require_control_plane().lookup_resumed_task_result(
                flow_run_id,
                planned_node_id,
                persist_result=self.persist_result,
                input_fingerprint=input_fp,
            )
            if hit:
                # Resume skip does not consume tag concurrency slots.
                return self._complete_from_cache(
                    flow_run_id,
                    planned_node_id,
                    cast(T, cached),
                    contribute_to_flow_state=not detach,
                    input_fingerprint=input_fp,
                )
            task_run, lease_ids = self._start_task_run(
                flow_run_id,
                planned_node_id,
                contribute_to_flow_state=not detach,
            )

        executor = _ACTIVE_SUBMIT_EXECUTOR.get()
        if executor is not None:
            ctx = contextvars.copy_context()
            cfuture = executor.submit(
                ctx.run,
                self._execute_and_finalize_submit,
                args,
                kwargs,
                task_run,
                lease_ids,
                input_fp,
            )
            return TaskFuture(
                task_run_id=str(task_run.task_run_id) if task_run is not None else None,
                planned_node_id=task_run.planned_node_id
                if task_run is not None
                else None,
                _cfuture=cfuture,
            )
        # Sequential / process / no shared pool: run body on the caller thread.
        return self._run_submitted_body_sync(
            args, kwargs, task_run, lease_ids, input_fp
        )

    def _run_submitted_body_sync(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        task_run: TaskRunRecord | None,
        lease_ids: list[str] | None = None,
        input_fingerprint: str | None = None,
    ) -> TaskFuture[T]:
        result = self._execute_and_finalize_submit(
            args, kwargs, task_run, lease_ids or [], input_fingerprint
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
        input_fingerprint: str | None = None,
    ) -> T:
        """Run the task body, then record COMPLETED/FAILED via the control plane.

        User work may run on a thread-pool worker; transitions always go through
        ``InMemoryControlPlane.record_task_event`` (Rust FSM + lock serialization),
        not a Python-only state write.
        """
        try:
            runner = _ACTIVE_TASK_RUNNER.get()
            use_process = task_run is not None and isinstance(
                runner, ProcessPoolTaskRunner
            )
            if use_process:
                # Resolve futures on the parent, then run the picklable body in a child.
                resolved_args = tuple(_resolve(v) for v in args)
                resolved_kwargs = {k: _resolve(v) for k, v in kwargs.items()}
                result = run_in_registered_process(
                    flow_run_id=task_run.flow_run_id,
                    task_run_id=task_run.task_run_id,
                    fn=self.fn,
                    args=resolved_args,
                    kwargs=resolved_kwargs,
                )
            elif task_run is not None:
                with bind_task_run(task_run.task_run_id, self.name):
                    result = self(*args, **kwargs)
            else:
                result = self(*args, **kwargs)
            if task_run is not None:
                try:
                    st = (
                        _require_control_plane()
                        .get_task_run(task_run.task_run_id)
                        .state
                    )
                except Exception:
                    st = None
                # Fence late COMPLETED after cancel/terminate pause.
                if st == RunState.CANCELLED:
                    return result
                if st in (RunState.RUNNING, RunState.PENDING, None):
                    return self._finalize_completed_task(
                        task_run,
                        result,
                        cache_hit=False,
                        input_fingerprint=input_fingerprint,
                    )
            return result
        except ProcessWorkerTerminated:
            # Cancel/terminate may kill the child before the task row flips to
            # CANCELLED — never invent FAILED for an intentional process kill.
            if task_run is not None:
                try:
                    st = (
                        _require_control_plane()
                        .get_task_run(task_run.task_run_id)
                        .state
                    )
                except Exception:
                    st = None
                if st in (RunState.RUNNING, RunState.PENDING):
                    try:
                        _require_control_plane().record_task_event(
                            task_run.task_run_id,
                            "task_cancelled",
                            {
                                "task_name": self.name,
                                "interrupt_reason": "process_terminated",
                            },
                        )
                    except Exception:
                        pass
            raise
        except Exception as exc:
            if isinstance(exc, FlowRunCancelled):
                raise
            if task_run is not None:
                # If ``task_completed`` progressed the FSM but persistence raised, do not emit FAILED.
                try:
                    st = (
                        _require_control_plane()
                        .get_task_run(task_run.task_run_id)
                        .state
                    )
                except Exception:
                    st = None
                if st == RunState.CANCELLED:
                    raise
                if st in (RunState.RUNNING, RunState.PENDING):
                    outcome = self._commit_task_failed(
                        task_run, exc, st, input_fingerprint=input_fingerprint
                    )
                    if outcome.committed == RunState.COMPLETED:
                        return outcome.result
                    raise_for_unsuccessful_task_terminal(
                        outcome,
                        original_exc=exc,
                        flow_run_id=task_run.flow_run_id,
                    )
            raise
        finally:
            self._release_tag_leases(lease_ids or [])

    def _complete_from_cache(
        self,
        flow_run_id: UUID,
        planned_node_id: str | None,
        value: T,
        *,
        contribute_to_flow_state: bool = True,
        map_index: int | None = None,
        input_fingerprint: str | None = None,
    ) -> TaskFuture[T]:
        task_run = _require_control_plane().create_task_run(
            flow_run_id,
            self.name,
            planned_node_id=planned_node_id,
            tags=self.tags,
            contribute_to_flow_state=contribute_to_flow_state,
        )
        _require_control_plane().record_task_events_batch(
            task_run.task_run_id,
            [
                ("task_pending", None),
                ("task_running", None),
            ],
        )
        # Cache hits advance the FSM for observability but do not re-fire user hooks.
        self._finalize_completed_task(
            task_run,
            value,
            cache_hit=True,
            map_index=map_index,
            input_fingerprint=input_fingerprint,
            fire_hooks=False,
        )
        return TaskFuture(
            value,
            task_run_id=str(task_run.task_run_id),
            planned_node_id=task_run.planned_node_id,
        )

    def _task_resume_payload(
        self,
        task_run: TaskRunRecord,
        result: Any,
        *,
        cache_hit: bool,
        map_index: int | None = None,
        input_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        extra = _require_control_plane().store_task_result_for_resume(
            task_run.flow_run_id,
            task_run.task_run_id,
            self.name,
            task_run.planned_node_id,
            result,
            persist_result=self.persist_result,
            map_index=map_index,
            input_fingerprint=input_fingerprint,
        )
        data: dict[str, Any] = {"task_name": self.name, **extra}
        if cache_hit:
            data["cache_hit"] = True
        return data

    def _commit_task_failed(
        self,
        task_run: TaskRunRecord,
        exc: BaseException,
        st: RunState,
        *,
        input_fingerprint: str | None = None,
    ) -> TaskTerminalOutcome:
        running = st == RunState.RUNNING
        return commit_task_terminal(
            self._transition_hooks,
            task_run,
            self.name,
            from_state=RunState.RUNNING if running else st,
            proposed=RunState.FAILED,
            event_type="task_failed",
            metadata={"task_name": self.name, "error": str(exc)},
            exception=exc,
            fire_hooks=running,
            allow_rewrite=running,
            persist_completed=lambda value: self._task_resume_payload(
                task_run, value, cache_hit=False, input_fingerprint=input_fingerprint
            ),
        )

    def _finalize_completed_task(
        self,
        task_run: TaskRunRecord,
        result: Any,
        *,
        cache_hit: bool,
        map_index: int | None = None,
        input_fingerprint: str | None = None,
        fire_hooks: bool = True,
    ) -> Any:
        try:
            st = _require_control_plane().get_task_run(task_run.task_run_id).state
        except Exception:
            st = None
        if st == RunState.CANCELLED:
            return result
        running = st == RunState.RUNNING
        outcome = commit_task_terminal(
            self._transition_hooks,
            task_run,
            self.name,
            from_state=RunState.RUNNING,
            proposed=RunState.COMPLETED,
            event_type="task_completed",
            metadata={"task_name": self.name},
            result=result,
            fire_hooks=fire_hooks,
            allow_rewrite=bool(fire_hooks and running),
            persist_completed=lambda value: self._task_resume_payload(
                task_run,
                value,
                cache_hit=cache_hit,
                map_index=map_index,
                input_fingerprint=input_fingerprint,
            ),
        )
        if outcome.committed == RunState.COMPLETED:
            return outcome.result
        raise_for_unsuccessful_task_terminal(outcome, flow_run_id=task_run.flow_run_id)

    def map(
        self,
        values: Iterable[Any],
        wait_for: Sequence[TaskFuture[Any] | SubflowFuture[Any] | GateFuture[Any]]
        | None = None,
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
    ) -> tuple[
        list[tuple[TaskRunRecord | None, Any, int, str | None]],
        list[TaskFuture[T] | None],
    ]:
        """Partition map values into resume cache-hits vs tasks that must execute."""
        flow_run_id = _ACTIVE_FLOW_RUN.get()
        to_run: list[tuple[TaskRunRecord | None, Any, int, str | None]] = []
        futures: list[TaskFuture[T] | None] = [None] * len(vals)
        planned_node_id: str | None = None
        for index, v in enumerate(vals):
            input_fp = fingerprint_task_inputs([_resolve(v)], {})
            if flow_run_id is not None:
                if planned_node_id is None:
                    planned_node_id = _require_control_plane().next_planned_node_id(
                        flow_run_id, self.name
                    )
                hit, cached = _require_control_plane().lookup_resumed_task_result(
                    flow_run_id,
                    planned_node_id,
                    map_index=index,
                    persist_result=self.persist_result,
                    input_fingerprint=input_fp,
                )
                if hit:
                    futures[index] = self._complete_from_cache(
                        flow_run_id,
                        planned_node_id,
                        cast(T, cached),
                        map_index=index,
                        input_fingerprint=input_fp,
                    )
                    continue
                task_run = _require_control_plane().create_task_run(
                    flow_run_id,
                    self.name,
                    planned_node_id=planned_node_id,
                    tags=self.tags,
                )
                if self.tags:
                    _require_control_plane().record_task_event(
                        task_run.task_run_id, "task_pending", None
                    )
                    th = self._transition_hooks
                    if th:
                        emit_task_single_hook_edge(
                            th,
                            task_run,
                            self.name,
                            RunState.SCHEDULED,
                            RunState.PENDING,
                            "task_pending",
                            None,
                        )
                else:
                    _require_control_plane().record_task_events_batch(
                        task_run.task_run_id,
                        [
                            ("task_pending", None),
                            ("task_running", None),
                        ],
                    )
                    th = self._transition_hooks
                    if th:
                        emit_task_transition_edges(
                            th, task_run, self.name, _TASK_HOOK_START_EDGES
                        )
                to_run.append((task_run, v, index, input_fp))
            else:
                to_run.append((None, v, index, input_fp))
        return to_run, futures

    def _finalize_map_task_runs(
        self,
        to_run: list[tuple[TaskRunRecord | None, Any, int, str | None]],
        outs: list[Any],
        futures: list[TaskFuture[T] | None],
    ) -> list[TaskFuture[T]]:
        for (task_run, _v, index, input_fp), raw in zip(to_run, outs, strict=True):
            if task_run is not None:
                raw = self._finalize_completed_task(
                    task_run,
                    raw,
                    cache_hit=False,
                    map_index=index,
                    input_fingerprint=input_fp,
                )
            futures[index] = TaskFuture(
                raw,
                task_run_id=str(task_run.task_run_id) if task_run is not None else None,
                planned_node_id=task_run.planned_node_id
                if task_run is not None
                else None,
            )
        return [f for f in futures if f is not None]

    def _fail_map_task_runs(
        self,
        to_run: list[tuple[TaskRunRecord | None, Any, int, str | None]],
        exc: Exception,
    ) -> None:
        cancelled_by_kill = isinstance(exc, ProcessWorkerTerminated)
        for task_run, _v, _index, _fp in to_run:
            if task_run is None:
                continue
            try:
                st = _require_control_plane().get_task_run(task_run.task_run_id).state
            except Exception:
                st = None
            if st not in (RunState.RUNNING, RunState.PENDING):
                continue
            if cancelled_by_kill:
                try:
                    _require_control_plane().record_task_event(
                        task_run.task_run_id,
                        "task_cancelled",
                        {
                            "task_name": self.name,
                            "interrupt_reason": "process_terminated",
                        },
                    )
                except Exception:
                    pass
                continue
            self._commit_task_failed(task_run, exc, st)

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
                    plane=_require_control_plane(),
                )
            except ConcurrencyLimitError:
                _require_control_plane().record_task_event(
                    task_run.task_run_id,
                    "task_cancelled",
                    {
                        "task_name": self.name,
                        "error": "tag concurrency limit denied (limit=0)",
                    },
                )
                raise
            _require_control_plane().record_task_event(
                task_run.task_run_id, "task_running", None
            )
            th = self._transition_hooks
            if th:
                emit_task_single_hook_edge(
                    th,
                    task_run,
                    self.name,
                    RunState.PENDING,
                    RunState.RUNNING,
                    "task_running",
                    None,
                )
        try:
            if task_run is not None:
                with bind_task_run(task_run.task_run_id, self.name):
                    return self.fn(value)
            return self.fn(value)
        finally:
            self._release_tag_leases(lease_ids)

    def _map_body_in_context(self, task_run: TaskRunRecord | None, value: Any) -> Any:
        """Run a map body with flow ContextVars + optional task binding copied in."""
        if self.tags:
            return self._run_tagged_map_body(task_run, value)
        if task_run is not None:
            with bind_task_run(task_run.task_run_id, self.name):
                return self.fn(value)
        return self.fn(value)

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
        to_run, futures = self._prepare_map_task_runs(vals)
        if not to_run:
            return [f for f in futures if f is not None]
        mx = min(len(to_run), runner.resolve_max_workers())

        def _one(item: tuple[TaskRunRecord | None, Any, int, str | None]) -> Any:
            task_run, value, _index, _fp = item
            ctx = contextvars.copy_context()
            return ctx.run(self._map_body_in_context, task_run, value)

        with ThreadPoolExecutor(max_workers=mx) as pool:
            try:
                outs = list(pool.map(_one, to_run))
            except Exception as exc:
                self._fail_map_task_runs(to_run, exc)
                raise
        return self._finalize_map_task_runs(to_run, outs, futures)

    def _map_process_pool(
        self,
        vals: list[Any],
        wait_for: list[TaskFuture[Any] | SubflowFuture[Any] | GateFuture[Any]] | None,
        runner: ProcessPoolTaskRunner,
    ) -> list[TaskFuture[T]]:
        """Map via registered child processes (parallel up to ``max_workers``).

        A thread-pool orchestrator waits on each registered process so cancel /
        terminate can SIGTERM→SIGKILL in-flight map items. Task body must be
        picklable (single positional arg per value).
        """
        if wait_for:
            wait(wait_for)
        if not vals:
            return []
        to_run, futures = self._prepare_map_task_runs(vals)
        if not to_run:
            return [f for f in futures if f is not None]
        fn = self.fn
        mx = min(len(to_run), runner.resolve_max_workers())

        def _one(item: tuple[TaskRunRecord | None, Any, int, str | None]) -> Any:
            task_run, v, _index, _fp = item
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
                        plane=_require_control_plane(),
                    )
                except ConcurrencyLimitError:
                    _require_control_plane().record_task_event(
                        task_run.task_run_id,
                        "task_cancelled",
                        {
                            "task_name": self.name,
                            "error": "tag concurrency limit denied (limit=0)",
                        },
                    )
                    raise
                _require_control_plane().record_task_event(
                    task_run.task_run_id, "task_running", None
                )
                th = self._transition_hooks
                if th:
                    emit_task_single_hook_edge(
                        th,
                        task_run,
                        self.name,
                        RunState.PENDING,
                        RunState.RUNNING,
                        "task_running",
                        None,
                    )
            try:
                if task_run is None:
                    return fn(v)
                try:
                    return run_in_registered_process(
                        flow_run_id=task_run.flow_run_id,
                        task_run_id=task_run.task_run_id,
                        fn=fn,
                        args=(v,),
                    )
                except ProcessWorkerTerminated:
                    try:
                        st = (
                            _require_control_plane()
                            .get_task_run(task_run.task_run_id)
                            .state
                        )
                    except Exception:
                        st = None
                    if st in (RunState.RUNNING, RunState.PENDING):
                        try:
                            _require_control_plane().record_task_event(
                                task_run.task_run_id,
                                "task_cancelled",
                                {
                                    "task_name": self.name,
                                    "interrupt_reason": "process_terminated",
                                },
                            )
                        except Exception:
                            pass
                    raise
            finally:
                self._release_tag_leases(lease_ids)

        with ThreadPoolExecutor(max_workers=mx) as pool:
            try:
                outs = list(pool.map(_one, to_run))
            except Exception as exc:
                self._fail_map_task_runs(to_run, exc)
                raise
        return self._finalize_map_task_runs(to_run, outs, futures)


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
    final_state: str = "wait_all",
    graph_mode: str = "auto",
) -> Callable[..., Any]:
    def decorate(f: Callable[..., T]) -> Callable[..., T]:
        flow_name = name or getattr(f, "__name__", "<flow>")
        declared_graph_mode = normalize_declared_graph_mode(graph_mode)
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
            submit_exec_token: contextvars.Token[ThreadPoolExecutor | None] | None = (
                None
            )
            if (
                isinstance(resolved_runner, ThreadPoolTaskRunner)
                and resolved_runner.resolve_max_workers() > 1
            ):
                submit_executor = ThreadPoolExecutor(
                    max_workers=resolved_runner.resolve_max_workers()
                )
                submit_exec_token = _ACTIVE_SUBMIT_EXECUTOR.set(submit_executor)
            elif isinstance(resolved_runner, ProcessPoolTaskRunner):
                # Wait on registered children off the coordinating thread so
                # cancel/terminate can observe in-flight PIDs (and flow code can
                # interleave after submit() before .result()).
                submit_executor = ThreadPoolExecutor(
                    max_workers=resolved_runner.resolve_max_workers()
                )
                submit_exec_token = _ACTIVE_SUBMIT_EXECUTOR.set(submit_executor)
            try:
                dep_run_id = _ACTIVE_DEPLOYMENT_RUN.get()
                parent_flow_run_id = None
                parent_task_run_id = None
                execution_mode = None
                resume_from: UUID | None = None
                dep_run: dict[str, Any] | None = None
                if dep_run_id is not None:
                    dep_run = _require_control_plane().get_deployment_run(dep_run_id)
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
                    resume_from = _require_control_plane().consume_pending_resume()
                # Prefer deployment resolved parameters for Case 6 param-guard.
                if dep_run is not None and isinstance(
                    dep_run.get("resolved_parameters"), dict
                ):
                    params_obj = cast(dict[str, Any], dep_run["resolved_parameters"])
                else:
                    params_obj = dict(kwargs)
                    if args:
                        params_obj = {**params_obj, "__args__": list(args)}
                parameters_fingerprint = fingerprint_parameters(params_obj)
                record = _require_control_plane().create_flow_run(
                    flow_name,
                    parent_flow_run_id=parent_flow_run_id,
                    parent_task_run_id=parent_task_run_id,
                    execution_mode=execution_mode,
                    resume_from_flow_run_id=resume_from,
                    parameters_fingerprint=parameters_fingerprint,
                )
                _ACTIVE_FLOW_RUN.set(record.run_id)
                flow_params = bound_flow_parameters(f, args, kwargs)
                with bind_flow_metadata(flow_name, flow_params):
                    if dep_run_id is not None:
                        _require_control_plane().attach_flow_run_to_deployment_run(
                            dep_run_id, record.run_id
                        )
                    manifest_info = _compile_forecast_for_flow(f, flow_name)
                    resolution = resolve_graph_mode(
                        declared_graph_mode,
                        fallback_required=bool(manifest_info["fallback_required"]),
                        manifest=manifest_info["manifest"],
                    )
                    _require_control_plane().save_flow_manifest(
                        run_id=record.run_id,
                        manifest=manifest_info["manifest"],
                        forecast=manifest_info["forecast"],
                        warnings=manifest_info["warnings"],
                        fallback_required=manifest_info["fallback_required"],
                        source=manifest_info["source"],
                    )
                    _require_control_plane().configure_flow_graph_mode(
                        record.run_id, resolution
                    )
                    start_transitions: list[tuple[RunState, UUID, str, int | None]] = [
                        (RunState.PENDING, uuid4(), "propose", 0),
                        (RunState.RUNNING, uuid4(), "start", 1),
                    ]
                    # Parent cancel can land after create_flow_run but before this
                    # optimistic PENDING→RUNNING batch; treat that as cancellation
                    # instead of surfacing a raw version-conflict ValueError.
                    pre_start = _require_control_plane().get_flow(record.run_id)
                    if pre_start.state == RunState.CANCELLED:
                        raise FlowRunCancelled(
                            f"flow run {record.run_id} was cancelled"
                        )
                    try:
                        batch_results = _require_control_plane().set_flow_states_batch(
                            record.run_id, start_transitions
                        )
                    except ValueError as exc:
                        if "version conflict" in str(exc):
                            current = _require_control_plane().get_flow(record.run_id)
                            if current.state == RunState.CANCELLED:
                                raise FlowRunCancelled(
                                    f"flow run {record.run_id} was cancelled"
                                ) from exc
                        raise
                    if fh:
                        emit_flow_hooks_for_batch(
                            fh,
                            record.run_id,
                            RunState.SCHEDULED,
                            start_transitions,
                            batch_results,
                        )
                    try:
                        result = f(*args, **kwargs)
                        _drain_submit_executor(submit_executor, submit_exec_token)
                        submit_executor = None
                        submit_exec_token = None
                        current = _require_control_plane().get_flow(record.run_id)
                        if current.state == RunState.CANCELLED:
                            raise FlowRunCancelled(
                                f"flow run {record.run_id} was cancelled"
                            )
                        if completion_mode == "wait_all":
                            _finalize_wait_all(record.run_id, result, fh, current)
                        else:
                            _finalize_explicit(record.run_id, result, fh, current)
                        return result
                    except FlowRunCancelled:
                        current = _require_control_plane().get_flow(record.run_id)
                        if current.state != RunState.CANCELLED:
                            prev = current.state
                            cancelled = _require_control_plane().set_flow_state(
                                record.run_id,
                                RunState.CANCELLED,
                                uuid4(),
                                "cancel",
                                expected_version=current.version,
                            )
                            if fh and cancelled.status == "applied":
                                emit_flow_transition(
                                    fh,
                                    record.run_id,
                                    prev,
                                    RunState.CANCELLED,
                                    "cancel",
                                )
                        raise
                    except FlowRunSchedulingHeld:
                        # Operator drain blocked a later submit — keep pause, do not FAILED.
                        current = _require_control_plane().get_flow(record.run_id)
                        if _require_control_plane().has_operator_pause(record.run_id):
                            if current.state == RunState.RUNNING:
                                try:
                                    _require_control_plane().set_flow_state(
                                        record.run_id,
                                        RunState.PAUSED,
                                        uuid4(),
                                        "operator_pause_drain_held",
                                        expected_version=current.version,
                                    )
                                except ValueError:
                                    pass
                            raise
                        raise
                    except FlowChildrenFailed:
                        raise
                    except Exception as exc:
                        current = _require_control_plane().get_flow(record.run_id)
                        if current.state == RunState.CANCELLED:
                            raise
                        # Pause/cancel lifecycle is set before PAUSED/CANCELLED
                        # settles; process kill must not invent FAILED mid-race.
                        if _require_control_plane().has_operator_interrupt(
                            record.run_id
                        ):
                            raise
                        failed = commit_flow_terminal(
                            fh,
                            record.run_id,
                            from_state=current.state,
                            proposed=RunState.FAILED,
                            kind="fail",
                            expected_version=current.version,
                            exception=exc,
                            allow_rewrite=current.state == RunState.RUNNING,
                        )
                        if failed.committed == RunState.COMPLETED:
                            _require_control_plane().set_flow_result(
                                record.run_id, failed.result
                            )
                            return failed.result
                        if failed.committed == RunState.CANCELLED:
                            raise FlowRunCancelled(
                                f"flow run {record.run_id} cancelled by "
                                "transition rewrite"
                            ) from exc
                        raise
            finally:
                _drain_submit_executor(submit_executor, submit_exec_token)
                _ACTIVE_FLOW_RUN.reset(flow_token)
                _ACTIVE_TASK_RUNNER.reset(token)

        return wrapped

    if fn is None:
        return decorate
    return decorate(fn)


def _drain_submit_executor(
    executor: ThreadPoolExecutor | None,
    token: contextvars.Token[ThreadPoolExecutor | None] | None,
) -> None:
    """Shut down the per-flow submit pool after outstanding bodies finish."""
    if token is not None:
        _ACTIVE_SUBMIT_EXECUTOR.reset(token)
    if executor is not None:
        executor.shutdown(wait=True)


def _finalize_explicit(
    flow_run_id: UUID,
    result: Any,
    fh: tuple[TransitionHookSpec, ...] | None,
    current: FlowRunRecord,
) -> None:
    if _require_control_plane().has_operator_pause(flow_run_id):
        # Drain-pending or settled PAUSED — do not auto-complete.
        _require_control_plane().set_flow_result(flow_run_id, result)
        return
    prev = current.state
    done = commit_flow_terminal(
        fh,
        flow_run_id,
        from_state=prev,
        proposed=RunState.COMPLETED,
        kind="complete",
        expected_version=current.version,
        result=result,
        allow_rewrite=prev == RunState.RUNNING,
    )
    if done.committed == RunState.COMPLETED:
        _require_control_plane().set_flow_result(flow_run_id, done.result)
        return
    if done.committed == RunState.CANCELLED:
        raise FlowRunCancelled(
            f"flow run {flow_run_id} cancelled by transition rewrite"
        )
    raise TransitionRewriteFailed(
        "flow completed state rewritten to FAILED",
        committed=done.committed.value,
        proposed=RunState.COMPLETED.value,
    )


def _finalize_wait_all(
    flow_run_id: UUID,
    result: Any,
    fh: tuple[TransitionHookSpec, ...] | None,
    current: FlowRunRecord,
) -> None:
    _require_control_plane().wait_contributing_children(flow_run_id)
    resolved = _require_control_plane().resolve_flow_terminal_state(flow_run_id)
    state_name = str(resolved.get("state", "FAILED"))
    kind = str(resolved.get("kind", "child_failed"))
    current = _require_control_plane().get_flow(flow_run_id)
    prev = current.state
    if prev == RunState.CANCELLED:
        raise FlowRunCancelled(f"flow run {flow_run_id} was cancelled")
    if _require_control_plane().has_operator_pause(flow_run_id):
        # Operator drain/terminate pause wins over auto-complete (incl. drain-pending).
        _require_control_plane().set_flow_result(flow_run_id, result)
        return

    try:
        proposed = RunState(state_name)
    except ValueError:
        proposed = RunState.FAILED
    if proposed not in {
        RunState.COMPLETED,
        RunState.FAILED,
        RunState.CANCELLED,
    }:
        proposed = RunState.FAILED
    if proposed == RunState.COMPLETED:
        apply_kind = "complete"
    elif proposed == RunState.CANCELLED:
        apply_kind = "child_cancelled"
    else:
        apply_kind = kind
    done = commit_flow_terminal(
        fh,
        flow_run_id,
        from_state=prev,
        proposed=proposed,
        kind=apply_kind,
        expected_version=current.version,
        result=result,
        allow_rewrite=prev == RunState.RUNNING,
    )
    stored = result
    if done.committed == RunState.COMPLETED:
        _require_control_plane().set_flow_result(flow_run_id, stored)
        return

    if done.committed == RunState.CANCELLED:
        raise FlowRunCancelled(
            f"flow run {flow_run_id} cancelled because a child was cancelled"
            if not done.rewritten
            else f"flow run {flow_run_id} cancelled by transition rewrite"
        )

    if done.rewritten:
        raise TransitionRewriteFailed(
            "flow completed state rewritten to FAILED",
            committed=done.committed.value,
            proposed=RunState.COMPLETED.value,
        )
    samples = resolved.get("sample_failures") or resolved.get("sample_incomplete") or []
    names = [
        str(s.get("task_name") or s.get("id")) for s in samples if isinstance(s, dict)
    ]
    detail = ", ".join(names[:5]) if names else kind
    raise FlowChildrenFailed(
        f"flow run {flow_run_id} failed: {kind} ({detail})",
        flow_run_id=str(flow_run_id),
        resolved_state=state_name,
        kind=kind,
        details=resolved,
    )


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


def _fingerprint_resolved_inputs(
    args: tuple[Any, ...], kwargs: dict[str, Any]
) -> str | None:
    """Fingerprint submit inputs after resolving nested futures."""
    try:
        resolved_args = [_resolve(v) for v in args]
        resolved_kwargs = {k: _resolve(v) for k, v in kwargs.items()}
    except Exception:
        return None
    return fingerprint_task_inputs(resolved_args, resolved_kwargs)


__all__ = [
    "TaskFuture",
    "TaskWrapper",
    "clear_forecast_cache",
    "flow",
    "set_control_plane",
    "task",
    "wait",
]
