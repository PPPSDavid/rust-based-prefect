"""Cooperative cancellation helpers for long-running task bodies."""

from __future__ import annotations

import time
from uuid import UUID

from .runtime import RunState


class FlowRunCancelled(RuntimeError):
    """Raised when a task observes its parent flow run was cancelled."""


def _control_plane():
    from .control_plane_registry import _require_control_plane

    return _require_control_plane()


def active_flow_run_id() -> UUID | None:
    from .decorators import _ACTIVE_FLOW_RUN

    return _ACTIVE_FLOW_RUN.get()


def assert_flow_not_cancelled(flow_run_id: UUID | None = None) -> None:
    plane = _control_plane()
    rid = flow_run_id or active_flow_run_id()
    if rid is None:
        return
    flow = plane.get_flow(rid)
    if flow.state == RunState.CANCELLED:
        raise FlowRunCancelled(f"flow run {rid} was cancelled")
    parent_id = flow.parent_flow_run_id
    if parent_id is not None:
        parent = plane.get_flow(parent_id)
        if parent.state == RunState.CANCELLED:
            raise FlowRunCancelled(f"parent flow run {parent_id} was cancelled")


def sleep_cancelable(seconds: float, *, poll_seconds: float = 0.25) -> None:
    """Sleep in short slices, aborting early if the active flow run is cancelled."""
    rid = active_flow_run_id()
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if rid is not None:
            assert_flow_not_cancelled(rid)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_seconds, remaining))
