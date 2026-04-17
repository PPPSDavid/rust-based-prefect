from __future__ import annotations

import contextvars
import inspect
import sys
import textwrap
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Generic, Iterable, Sequence, TypeVar
from uuid import UUID, uuid4

from .hooks import TransitionContext, TransitionHookSpec, compile_transition_hooks, dispatch_transition_hooks
from .runtime import InMemoryControlPlane, RunState, SetStateResult, TaskRunRecord
from .task_runners import MapTaskRunner, ProcessPoolTaskRunner, default_task_runner_from_env

# Reused when ``transition_hooks`` is set (avoids per-submit list literals on the hot path).
_TASK_HOOK_START_EDGES: tuple[tuple[RunState, RunState, str, dict[str, Any] | None], ...] = (
    (RunState.SCHEDULED, RunState.PENDING, "task_pending", None),
    (RunState.PENDING, RunState.RUNNING, "task_running", None),
)

T = TypeVar("T")

_CONTROL_PLANE = InMemoryControlPlane()

_ACTIVE_TASK_RUNNER: contextvars.ContextVar[MapTaskRunner | ProcessPoolTaskRunner | None] = contextvars.ContextVar(
    "ironflow_active_task_runner", default=None
)


@dataclass
class TaskFuture(Generic[T]):
    value: T
    task_run_id: str | None = None
    planned_node_id: str | None = None

    def result(self) -> T:
        return self.value


def wait(futures: Sequence["TaskFuture[Any]"]) -> list[Any]:
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
    ) -> None:
        self.fn = fn
        self.name = name or fn.__name__
        self._transition_hooks = transition_hooks
        wraps(fn)(self)

    def __call__(self, *args: Any, **kwargs: Any) -> T:
        resolved_args = [_resolve(v) for v in args]
        resolved_kwargs = {k: _resolve(v) for k, v in kwargs.items()}
        return self.fn(*resolved_args, **resolved_kwargs)

    def submit(self, *args: Any, wait_for: Sequence[TaskFuture[Any]] | None = None, **kwargs: Any) -> TaskFuture[T]:
        if wait_for:
            wait(wait_for)

        latest_flow = _CONTROL_PLANE.latest_flow()
        task_run = None
        if latest_flow is not None:
            planned_node_id = _CONTROL_PLANE.next_planned_node_id(latest_flow.run_id, self.name)
            task_run = _CONTROL_PLANE.create_task_run(
                latest_flow.run_id, self.name, planned_node_id=planned_node_id
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
                _emit_task_transition_edges(th, task_run, self.name, _TASK_HOOK_START_EDGES)

        try:
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
            return TaskFuture(
                result,
                task_run_id=str(task_run.task_run_id) if task_run is not None else None,
                planned_node_id=task_run.planned_node_id if task_run is not None else None,
            )
        except Exception as exc:
            if task_run is not None:
                # If ``task_completed`` progressed the FSM but persistence raised, do not emit FAILED.
                try:
                    st = _CONTROL_PLANE.get_task_run(task_run.task_run_id).state
                except Exception:
                    st = None
                if st == RunState.RUNNING:
                    _CONTROL_PLANE.record_task_event(
                        task_run.task_run_id, "task_failed", {"task_name": self.name, "error": str(exc)}
                    )
                    th = self._transition_hooks
                    if th:
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

    def map(
        self, values: Iterable[Any], wait_for: Sequence[TaskFuture[Any]] | None = None
    ) -> list[TaskFuture[T]]:
        runner = _ACTIVE_TASK_RUNNER.get()
        if runner is None:
            runner = default_task_runner_from_env()
        vals = list(values)
        wf: list[TaskFuture[Any]] | None = list(wait_for) if wait_for else None
        if isinstance(runner, ProcessPoolTaskRunner):
            return self._map_process_pool(vals, wf, runner)
        assert isinstance(runner, MapTaskRunner)
        return runner.map_values(
            self,
            vals,
            wf,
            wait,
            lambda v: self.submit(v, wait_for=None),
        )

    def _map_process_pool(
        self,
        vals: list[Any],
        wait_for: list[TaskFuture[Any]] | None,
        runner: ProcessPoolTaskRunner,
    ) -> list[TaskFuture[T]]:
        """Map via child processes; task body must be picklable (single positional arg per value)."""
        if wait_for:
            wait(wait_for)
        if not vals:
            return []
        mx = min(len(vals), runner.resolve_max_workers())
        metas: list[tuple[Any, Any]] = []
        for v in vals:
            latest_flow = _CONTROL_PLANE.latest_flow()
            task_run = None
            if latest_flow is not None:
                planned_node_id = _CONTROL_PLANE.next_planned_node_id(latest_flow.run_id, self.name)
                task_run = _CONTROL_PLANE.create_task_run(
                    latest_flow.run_id, self.name, planned_node_id=planned_node_id
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
                    _emit_task_transition_edges(th, task_run, self.name, _TASK_HOOK_START_EDGES)
            metas.append((task_run, v))

        fn = self.fn
        with ProcessPoolExecutor(max_workers=mx) as pool:
            outs = list(pool.map(fn, [m[1] for m in metas]))

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
                    task_run_id=str(task_run.task_run_id) if task_run is not None else None,
                    planned_node_id=task_run.planned_node_id if task_run is not None else None,
                )
            )
        return out


def task(
    fn: Callable[..., T] | None = None,
    *,
    name: str | None = None,
    transition_hooks: Sequence[TransitionHookSpec] | None = None,
) -> Callable[..., Any]:
    def decorate(f: Callable[..., T]) -> TaskWrapper:
        compiled = compile_transition_hooks(transition_hooks)
        return TaskWrapper(f, name=name, transition_hooks=compiled)

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
        flow_name = name or f.__name__
        resolved_runner = task_runner if task_runner is not None else default_task_runner_from_env()
        compiled_flow_hooks = compile_transition_hooks(transition_hooks)

        @wraps(f)
        def wrapped(*args: Any, **kwargs: Any) -> T:
            token = _ACTIVE_TASK_RUNNER.set(resolved_runner)
            fh = compiled_flow_hooks
            try:
                record = _CONTROL_PLANE.create_flow_run(flow_name)
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
                batch_results = _CONTROL_PLANE.set_flow_states_batch(record.run_id, start_transitions)
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
                    prev = _CONTROL_PLANE.get_flow(record.run_id).state
                    done = _CONTROL_PLANE.set_flow_state(
                        record.run_id, RunState.COMPLETED, uuid4(), "complete", expected_version=2
                    )
                    if fh and done.status == "applied":
                        _emit_flow_transition(fh, record.run_id, prev, RunState.COMPLETED, "complete")
                    return result
                except Exception:
                    prev = _CONTROL_PLANE.get_flow(record.run_id).state
                    failed = _CONTROL_PLANE.set_flow_state(
                        record.run_id, RunState.FAILED, uuid4(), "fail", expected_version=2
                    )
                    if fh and failed.status == "applied":
                        _emit_flow_transition(fh, record.run_id, prev, RunState.FAILED, "fail")
                    raise
            finally:
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
    return value


def _compile_forecast_for_flow(flow_fn: Callable[..., Any], flow_name: str) -> dict[str, Any]:
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
                "warnings": ["Static planner not available; runtime fallback DAG will be used."],
                "fallback_required": True,
                "source": "runtime",
            }

    try:
        source = textwrap.dedent(inspect.getsource(flow_fn))
        result = compile_and_forecast(source, flow_name=flow_name)
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
