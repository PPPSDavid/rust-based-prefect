"""First-attempt startup for a newly created flow run."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from .cancellation import FlowRunCancelled
from .control_plane_registry import _require_control_plane
from .forecast_compile import _compile_forecast_for_flow
from .graph_mode import GraphModeLiteral, resolve_graph_mode
from .hooks import emit_flow_hooks_for_batch
from .runtime import FlowRunRecord, RunState


def start_fresh_flow_attempt(
    *,
    dep_run_id: UUID | None,
    record: FlowRunRecord,
    flow_fn: Callable[..., Any],
    flow_name: str,
    declared_graph_mode: GraphModeLiteral,
    hooks: tuple[Any, ...] | None,
) -> None:
    """Attach, compile, and move a new flow run from SCHEDULED to RUNNING."""
    plane = _require_control_plane()
    if dep_run_id is not None:
        plane.attach_flow_run_to_deployment_run(dep_run_id, record.run_id)
    manifest_info = _compile_forecast_for_flow(flow_fn, flow_name)
    resolution = resolve_graph_mode(
        declared_graph_mode,
        fallback_required=bool(manifest_info["fallback_required"]),
        manifest=manifest_info["manifest"],
    )
    plane.save_flow_manifest(
        run_id=record.run_id,
        manifest=manifest_info["manifest"],
        forecast=manifest_info["forecast"],
        warnings=manifest_info["warnings"],
        fallback_required=manifest_info["fallback_required"],
        source=manifest_info["source"],
    )
    plane.configure_flow_graph_mode(record.run_id, resolution)
    start_transitions: list[tuple[RunState, UUID, str, int | None]] = [
        (RunState.PENDING, uuid4(), "propose", 0),
        (RunState.RUNNING, uuid4(), "start", 1),
    ]
    # Parent cancel can land after create_flow_run but before this
    # optimistic PENDING→RUNNING batch; treat that as cancellation
    # instead of surfacing a raw version-conflict ValueError.
    pre_start = plane.get_flow(record.run_id)
    if pre_start.state == RunState.CANCELLED:
        raise FlowRunCancelled(f"flow run {record.run_id} was cancelled")
    try:
        batch_results = plane.set_flow_states_batch(record.run_id, start_transitions)
    except ValueError as exc:
        if "version conflict" in str(exc):
            current = plane.get_flow(record.run_id)
            if current.state == RunState.CANCELLED:
                raise FlowRunCancelled(
                    f"flow run {record.run_id} was cancelled"
                ) from exc
        raise
    if hooks:
        emit_flow_hooks_for_batch(
            hooks,
            record.run_id,
            RunState.SCHEDULED,
            start_transitions,
            batch_results,
        )
