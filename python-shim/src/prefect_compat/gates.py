"""Temporal gate tasks: block downstream work until a scheduled open time."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Generic, TypeVar, cast
from collections.abc import Sequence
from uuid import UUID

from .cancellation import FlowRunCancelled, assert_flow_not_cancelled, sleep_cancelable
from .decorators import (
    _ACTIVE_FLOW_RUN,
    TaskFuture,
    wait,
)
from .runtime import RunState

T = TypeVar("T")
_UNSET = object()

# Definitional defaults (Python-only safeguards; no server policy yet).
DEFAULT_GATE_MAX_WAIT: timedelta = timedelta(days=1)
DEFAULT_GATE_AFTER_MAX_WAIT: timedelta = timedelta(days=1)


class GateWaitTooLongError(ValueError):
    """Raised when a gate would wait longer than the effective max_wait policy."""


def _control_plane():
    from .decorators import _CONTROL_PLANE

    return _CONTROL_PLANE


def _effective_max_wait(max_wait: timedelta | None) -> timedelta:
    return max_wait if max_wait is not None else DEFAULT_GATE_MAX_WAIT


def _resolve_open_at(
    *,
    until: datetime | None,
    after: timedelta | None,
) -> datetime:
    if until is not None and after is not None:
        raise ValueError("gate submit accepts only one of until= or after=")
    if until is not None:
        dt = until if until.tzinfo is not None else until.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    if after is not None:
        return datetime.now(UTC) + after
    raise ValueError("gate submit requires until= or after=")


def _validate_gate_wait(open_at: datetime, max_wait: timedelta) -> None:
    wait_seconds = (open_at - datetime.now(UTC)).total_seconds()
    if wait_seconds > max_wait.total_seconds():
        raise GateWaitTooLongError(
            f"gate would wait {wait_seconds:.0f}s; limit is {max_wait.total_seconds():.0f}s "
            f"({max_wait}). Pass max_wait=... to opt in to a longer wait."
        )


@dataclass
class GateFuture(Generic[T]):
    task_run_id: UUID
    planned_node_id: str | None
    open_at: datetime
    _value: Any = _UNSET

    def result(self) -> T:
        if self._value is not _UNSET:
            return cast(T, self._value)

        plane = _control_plane()
        flow_run_id = _ACTIVE_FLOW_RUN.get()
        task_id = self.task_run_id
        open_at = self.open_at

        if open_at <= datetime.now(UTC):
            plane.complete_gate_task(task_id)
            self._value = None
            return cast(T, self._value)

        if flow_run_id is not None:
            plane.pause_flow_for_gate(flow_run_id, task_id)

        try:
            deadline = time.monotonic() + max(
                0.0, (open_at - datetime.now(UTC)).total_seconds() + 30.0
            )
            poll = 0.05
            while time.monotonic() < deadline:
                if flow_run_id is not None:
                    try:
                        assert_flow_not_cancelled(flow_run_id)
                    except FlowRunCancelled:
                        plane.cancel_gate_task(task_id)
                        raise
                plane.tick_gate_tasks()
                task = plane.get_task_run(task_id)
                if task.state == RunState.COMPLETED:
                    break
                if datetime.now(UTC) >= open_at:
                    plane.complete_gate_task(task_id)
                    break
                remaining = (open_at - datetime.now(UTC)).total_seconds()
                if remaining > 0:
                    sleep_cancelable(min(poll, remaining), poll_seconds=poll)
            else:
                plane.fail_gate_task(task_id, "gate wait timed out")
                raise TimeoutError(f"gate {task_id} did not open before deadline")

            task = plane.get_task_run(task_id)
            if task.state != RunState.COMPLETED:
                plane.complete_gate_task(task_id)
        finally:
            if flow_run_id is not None:
                plane.resume_flow_from_gate(flow_run_id)

        self._value = None
        return cast(T, self._value)


class GateWrapper:
    """Factory for temporal gate task runs inside an active @flow."""

    def __init__(
        self,
        *,
        name: str = "gate",
        max_wait: timedelta | None = None,
    ) -> None:
        self.name = name
        self.max_wait = max_wait

    def submit(
        self,
        *,
        wait_for: Sequence[TaskFuture[Any] | GateFuture[Any]] | None = None,
        until: datetime | None = None,
        after: timedelta | None = None,
        max_wait: timedelta | None = None,
    ) -> GateFuture[None]:
        if wait_for:
            wait(wait_for)

        flow_run_id = _ACTIVE_FLOW_RUN.get()
        if flow_run_id is None:
            raise RuntimeError(
                "gate submit() requires an active flow run; call from inside a @flow function"
            )

        open_at = _resolve_open_at(until=until, after=after)
        effective = _effective_max_wait(max_wait if max_wait is not None else self.max_wait)
        _validate_gate_wait(open_at, effective)

        plane = _control_plane()
        task_name = f"gate:{self.name}"
        planned_node_id = plane.next_planned_node_id(flow_run_id, task_name)
        task_run = plane.create_task_run(
            flow_run_id,
            task_name,
            planned_node_id=planned_node_id,
            kind="gate",
            gate_open_at=open_at.isoformat(),
        )
        plane.record_task_event(
            task_run.task_run_id,
            "task_pending",
            {
                "gate": self.name,
                "open_at": open_at.isoformat(),
                "max_wait_seconds": effective.total_seconds(),
            },
        )

        wait_seconds = (open_at - datetime.now(UTC)).total_seconds()
        if wait_seconds <= 0:
            plane.complete_gate_task(task_run.task_run_id)
            return GateFuture(
                task_run_id=task_run.task_run_id,
                planned_node_id=planned_node_id,
                open_at=open_at,
                _value=None,
            )

        return GateFuture(
            task_run_id=task_run.task_run_id,
            planned_node_id=planned_node_id,
            open_at=open_at,
        )


def gate(*, name: str = "gate", max_wait: timedelta | None = None) -> GateWrapper:
    """Create a temporal gate barrier task (use ``.submit(until=...)`` or ``after=...``)."""
    return GateWrapper(name=name, max_wait=max_wait)
