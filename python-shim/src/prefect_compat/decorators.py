from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Generic, Iterable, Sequence, TypeVar
from uuid import uuid4

from .runtime import InMemoryControlPlane, RunState

T = TypeVar("T")

_CONTROL_PLANE = InMemoryControlPlane()


@dataclass
class TaskFuture(Generic[T]):
    value: T

    def result(self) -> T:
        return self.value


def wait(futures: Sequence["TaskFuture[Any]"]) -> list[Any]:
    return [future.result() for future in futures]


def set_control_plane(control_plane: InMemoryControlPlane) -> None:
    global _CONTROL_PLANE
    _CONTROL_PLANE = control_plane


class TaskWrapper:
    def __init__(self, fn: Callable[..., T], name: str | None = None) -> None:
        self.fn = fn
        self.name = name or fn.__name__
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
            task_run = _CONTROL_PLANE.create_task_run(latest_flow.run_id, self.name)
            _CONTROL_PLANE.record_task_event(task_run.task_run_id, "task_pending")
            _CONTROL_PLANE.record_task_event(task_run.task_run_id, "task_running")

        try:
            result = self(*args, **kwargs)
            if task_run is not None:
                _CONTROL_PLANE.record_task_event(
                    task_run.task_run_id, "task_completed", {"task_name": self.name}
                )
            return TaskFuture(result)
        except Exception as exc:
            if task_run is not None:
                _CONTROL_PLANE.record_task_event(
                    task_run.task_run_id, "task_failed", {"task_name": self.name, "error": str(exc)}
                )
            raise

    def map(
        self, values: Iterable[Any], wait_for: Sequence[TaskFuture[Any]] | None = None
    ) -> list[TaskFuture[T]]:
        return [self.submit(v, wait_for=wait_for) for v in values]


def task(fn: Callable[..., T] | None = None, *, name: str | None = None) -> Callable[..., Any]:
    def decorate(f: Callable[..., T]) -> TaskWrapper:
        return TaskWrapper(f, name=name)

    if fn is None:
        return decorate
    return decorate(fn)


def flow(fn: Callable[..., T] | None = None, *, name: str | None = None) -> Callable[..., Any]:
    def decorate(f: Callable[..., T]) -> Callable[..., T]:
        flow_name = name or f.__name__

        @wraps(f)
        def wrapped(*args: Any, **kwargs: Any) -> T:
            record = _CONTROL_PLANE.create_flow_run(flow_name)
            _CONTROL_PLANE.set_flow_state(
                record.run_id, RunState.PENDING, uuid4(), "propose", expected_version=0
            )
            _CONTROL_PLANE.set_flow_state(
                record.run_id, RunState.RUNNING, uuid4(), "start", expected_version=1
            )
            try:
                result = f(*args, **kwargs)
                _CONTROL_PLANE.set_flow_state(
                    record.run_id, RunState.COMPLETED, uuid4(), "complete", expected_version=2
                )
                return result
            except Exception:
                _CONTROL_PLANE.set_flow_state(
                    record.run_id, RunState.FAILED, uuid4(), "fail", expected_version=2
                )
                raise

        return wrapped

    if fn is None:
        return decorate
    return decorate(fn)


def _resolve(value: Any) -> Any:
    if isinstance(value, TaskFuture):
        return value.result()
    return value
