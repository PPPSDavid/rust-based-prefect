"""Pre-commit rewrite + one FSM apply + observe-on-committed helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NoReturn
from uuid import UUID, uuid4

from .cancellation import FlowRunCancelled
from .control_plane_registry import _require_control_plane
from .errors import TransitionRewriteFailed
from .hooks import (
    TransitionContext,
    TransitionDecision,
    TransitionHookSpec,
    emit_flow_transition,
    emit_task_single_hook_edge,
    resolve_terminal_rewrite,
)
from .runtime import RunState, TaskRunRecord

_TASK_EVENT_FOR_STATE: dict[RunState, str] = {
    RunState.COMPLETED: "task_completed",
    RunState.FAILED: "task_failed",
    RunState.CANCELLED: "task_cancelled",
}

_FLOW_KIND_FOR_STATE: dict[RunState, str] = {
    RunState.COMPLETED: "complete",
    RunState.FAILED: "fail",
    RunState.CANCELLED: "cancel",
}


@dataclass(frozen=True)
class FlowTerminalOutcome:
    committed: RunState
    rewritten: bool
    status: str
    result: Any
    kind: str


@dataclass(frozen=True)
class TaskTerminalOutcome:
    committed: RunState
    rewritten: bool
    result: Any
    event_type: str
    message: str | None = None


def _rewrite_audit_metadata(
    metadata: dict[str, Any] | None,
    *,
    proposed: RunState,
    committed: RunState,
) -> dict[str, Any] | None:
    if proposed == committed:
        return metadata
    data = dict(metadata or {})
    data["proposed_to_state"] = proposed.value
    data["rewritten_from"] = proposed.value
    return data


def _flow_kind(proposed_kind: str, proposed: RunState, committed: RunState) -> str:
    if proposed == committed:
        return proposed_kind
    if committed == RunState.CANCELLED and proposed_kind == "child_cancelled":
        return "child_cancelled"
    return _FLOW_KIND_FOR_STATE[committed]


def _task_event(proposed_event: str, proposed: RunState, committed: RunState) -> str:
    if proposed == committed:
        return proposed_event
    return _TASK_EVENT_FOR_STATE[committed]


def _observe_skip_ids(
    *, rewritten: bool, invoked_ids: frozenset[int], winner_id: int | None
) -> frozenset[int]:
    """Avoid double-calling hooks already invoked on the proposed edge.

    After a rewrite, only skip the winning spec so observers of the committed
    edge still run. With no rewrite, skip everyone already invoked (proposed
    equals committed).
    """
    if rewritten:
        return frozenset({winner_id} if winner_id is not None else ())
    return invoked_ids


def _resolve_decision(
    specs: tuple[TransitionHookSpec, ...] | None,
    ctx: TransitionContext,
    *,
    allow_rewrite: bool,
) -> tuple[TransitionDecision | None, frozenset[int], int | None]:
    if not allow_rewrite or not specs:
        return None, frozenset(), None
    probe = resolve_terminal_rewrite(specs, ctx)
    return probe.decision, probe.invoked_ids, probe.winner_id


def commit_flow_terminal(
    specs: tuple[TransitionHookSpec, ...] | None,
    flow_run_id: UUID,
    *,
    from_state: RunState,
    proposed: RunState,
    kind: str,
    expected_version: int | None,
    metadata: dict[str, Any] | None = None,
    result: Any = None,
    exception: BaseException | None = None,
    allow_rewrite: bool = True,
) -> FlowTerminalOutcome:
    """Rewrite (optional) → ``set_flow_state`` once → observe the committed edge."""
    decision, invoked_ids, winner_id = _resolve_decision(
        specs,
        TransitionContext(
            kind="flow",
            flow_run_id=flow_run_id,
            from_state=from_state,
            to_state=proposed,
            transition_kind=kind,
            metadata=metadata,
            exception=exception,
            proposed_to_state=proposed,
        ),
        allow_rewrite=allow_rewrite,
    )
    committed = decision.to_state if decision is not None else proposed
    rewritten = decision is not None and decision.to_state != proposed
    applied_kind = _flow_kind(kind, proposed, committed)
    meta = _rewrite_audit_metadata(metadata, proposed=proposed, committed=committed)
    effective_result = decision.result if rewritten else result
    applied = _require_control_plane().set_flow_state(
        flow_run_id,
        committed,
        uuid4(),
        applied_kind,
        expected_version=expected_version,
    )
    if specs and applied.status == "applied":
        emit_flow_transition(
            specs,
            flow_run_id,
            from_state,
            committed,
            applied_kind,
            meta,
            proposed_to_state=proposed if rewritten else None,
            exception=exception,
            skip_ids=_observe_skip_ids(
                rewritten=rewritten, invoked_ids=invoked_ids, winner_id=winner_id
            ),
        )
    return FlowTerminalOutcome(
        committed=committed,
        rewritten=rewritten,
        status=applied.status,
        result=effective_result,
        kind=applied_kind,
    )


def commit_task_terminal(
    specs: tuple[TransitionHookSpec, ...] | None,
    task_run: TaskRunRecord,
    task_name: str,
    *,
    from_state: RunState,
    proposed: RunState,
    event_type: str,
    metadata: dict[str, Any] | None = None,
    result: Any = None,
    exception: BaseException | None = None,
    fire_hooks: bool = True,
    allow_rewrite: bool = True,
    persist_completed: Callable[[Any], dict[str, Any]] | None = None,
) -> TaskTerminalOutcome:
    """Rewrite (optional) → ``record_task_event`` once → observe the committed edge."""
    decision, invoked_ids, winner_id = _resolve_decision(
        specs,
        TransitionContext(
            kind="task",
            flow_run_id=task_run.flow_run_id,
            from_state=from_state,
            to_state=proposed,
            event_type=event_type,
            task_run_id=task_run.task_run_id,
            task_name=task_name,
            planned_node_id=task_run.planned_node_id,
            metadata=metadata,
            exception=exception,
            proposed_to_state=proposed,
        ),
        allow_rewrite=allow_rewrite and fire_hooks,
    )
    committed = decision.to_state if decision is not None else proposed
    rewritten = decision is not None and decision.to_state != proposed
    applied_event = _task_event(event_type, proposed, committed)
    effective_result = (
        decision.result if rewritten and committed == RunState.COMPLETED else result
    )
    message = decision.message if decision is not None else None
    data: dict[str, Any] | None = dict(metadata or {}) if metadata else None
    if committed == RunState.COMPLETED and persist_completed is not None:
        extra = persist_completed(effective_result)
        data = {**(data or {}), **extra}
    elif rewritten and committed == RunState.FAILED and message:
        data = dict(data or {})
        data.setdefault("error", message)
    data = _rewrite_audit_metadata(data, proposed=proposed, committed=committed)
    _require_control_plane().record_task_event(
        task_run.task_run_id, applied_event, data
    )
    if specs and fire_hooks:
        emit_task_single_hook_edge(
            specs,
            task_run,
            task_name,
            from_state,
            committed,
            applied_event,
            data,
            proposed_to_state=proposed if rewritten else None,
            exception=exception,
            skip_ids=_observe_skip_ids(
                rewritten=rewritten, invoked_ids=invoked_ids, winner_id=winner_id
            ),
        )
    return TaskTerminalOutcome(
        committed=committed,
        rewritten=rewritten,
        result=effective_result,
        event_type=applied_event,
        message=message,
    )


def raise_for_unsuccessful_task_terminal(
    outcome: TaskTerminalOutcome,
    *,
    original_exc: BaseException | None = None,
    flow_run_id: UUID | None = None,
) -> NoReturn:
    """Raise after a task terminal commit that did not stay COMPLETED."""
    if outcome.committed == RunState.CANCELLED:
        suffix = f" (flow run {flow_run_id})" if flow_run_id is not None else ""
        raise FlowRunCancelled(f"task run cancelled by transition rewrite{suffix}")
    if outcome.rewritten:
        raise TransitionRewriteFailed(
            outcome.message or "task completed state rewritten to FAILED",
            committed=outcome.committed.value,
            proposed=RunState.COMPLETED.value,
        )
    if original_exc is not None:
        raise original_exc
    raise TransitionRewriteFailed(
        outcome.message or f"task ended in {outcome.committed.value}",
        committed=outcome.committed.value,
    )
