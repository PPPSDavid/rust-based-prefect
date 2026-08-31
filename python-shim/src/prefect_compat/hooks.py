"""Unified (from_state, to_state) transition hooks for flows and tasks."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from .runtime import RunState, SetStateResult, TaskRunRecord

logger = logging.getLogger(__name__)

TransitionKind = Literal["flow", "task"]
TransitionHookMode = Literal["observe", "rewrite"]

REWRITE_TERMINALS: frozenset[RunState] = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
)


@dataclass
class TransitionContext:
    """Facts about a proposed or committed control-plane transition."""

    kind: TransitionKind
    flow_run_id: UUID
    from_state: RunState
    to_state: RunState
    transition_kind: str | None = None
    event_type: str | None = None
    task_run_id: UUID | None = None
    task_name: str | None = None
    planned_node_id: str | None = None
    metadata: dict[str, Any] | None = None
    exception: BaseException | None = None
    proposed_to_state: RunState | None = None


@dataclass(frozen=True)
class TransitionDecision:
    """Rewrite a proposed RUNNING→terminal destination before the FSM apply."""

    to_state: RunState
    result: Any = None
    message: str | None = None


@dataclass(frozen=True)
class TransitionHookSpec:
    fn: Callable[[TransitionContext], Any]
    from_state: RunState | None = None
    to_state: RunState | None = None
    mode: TransitionHookMode = "observe"


def on_transition(
    fn: Callable[[TransitionContext], Any],
    *,
    from_state: RunState | None = None,
    to_state: RunState | None = None,
    mode: TransitionHookMode = "observe",
) -> TransitionHookSpec:
    """Register ``fn`` for edges matching optional ``from_state`` / ``to_state``.

    ``mode="observe"`` (default) runs after a successful commit; the return
    value is ignored. ``mode="rewrite"`` runs before commit on proposed
    ``RUNNING`` → terminal edges and may return a ``TransitionDecision``.
    """
    if mode not in ("observe", "rewrite"):
        raise ValueError(f"mode must be 'observe' or 'rewrite', got {mode!r}")
    return TransitionHookSpec(
        fn=fn, from_state=from_state, to_state=to_state, mode=mode
    )


def compile_transition_hooks(
    specs: Sequence[TransitionHookSpec] | None,
) -> tuple[TransitionHookSpec, ...] | None:
    """Freeze hook registration order for dispatch; returns ``None`` when there is nothing to run."""
    if not specs:
        return None
    return tuple(specs)


def has_rewrite_specs(specs: tuple[TransitionHookSpec, ...] | None) -> bool:
    """True when at least one compiled spec is a pre-commit rewrite handler."""
    if not specs:
        return False
    return any(spec.mode == "rewrite" for spec in specs)


def _edge_matches(spec: TransitionHookSpec, ctx: TransitionContext) -> bool:
    from_ok = spec.from_state is None or spec.from_state == ctx.from_state
    to_ok = spec.to_state is None or spec.to_state == ctx.to_state
    return from_ok and to_ok


def dispatch_transition_hooks(
    specs: tuple[TransitionHookSpec, ...] | None, ctx: TransitionContext
) -> None:
    """Run matching observe hooks in registration order. Exceptions are logged and swallowed."""
    if not specs:
        return
    for spec in specs:
        if spec.mode != "observe":
            continue
        if not _edge_matches(spec, ctx):
            continue
        try:
            spec.fn(ctx)
        except Exception:
            logger.exception(
                "transition hook failed kind=%s flow_run_id=%s from=%s to=%s",
                ctx.kind,
                ctx.flow_run_id,
                ctx.from_state,
                ctx.to_state,
            )


def resolve_terminal_rewrite(
    specs: tuple[TransitionHookSpec, ...] | None, ctx: TransitionContext
) -> TransitionDecision | None:
    """First matching rewrite handler that returns a legal terminal decision wins.

    Handlers still match the original proposed edge (no cascade). Exceptions and
    illegal returns are logged; those handlers are skipped.
    """
    if not specs:
        return None
    if ctx.from_state != RunState.RUNNING or ctx.to_state not in REWRITE_TERMINALS:
        return None
    for spec in specs:
        if spec.mode != "rewrite":
            continue
        if not _edge_matches(spec, ctx):
            continue
        try:
            raw = spec.fn(ctx)
        except Exception:
            logger.exception(
                "transition rewrite handler failed kind=%s flow_run_id=%s from=%s to=%s",
                ctx.kind,
                ctx.flow_run_id,
                ctx.from_state,
                ctx.to_state,
            )
            continue
        if raw is None:
            continue
        decision = _normalize_rewrite_decision(raw, ctx)
        if decision is None:
            continue
        return decision
    return None


def _normalize_rewrite_decision(
    raw: Any, ctx: TransitionContext
) -> TransitionDecision | None:
    if not isinstance(raw, TransitionDecision):
        logger.warning(
            "transition rewrite ignored (expected TransitionDecision) kind=%s "
            "flow_run_id=%s from=%s to=%s got=%s",
            ctx.kind,
            ctx.flow_run_id,
            ctx.from_state,
            ctx.to_state,
            type(raw).__name__,
        )
        return None
    if raw.to_state not in REWRITE_TERMINALS:
        logger.warning(
            "transition rewrite ignored (illegal to_state=%s) kind=%s "
            "flow_run_id=%s from=%s proposed=%s",
            raw.to_state,
            ctx.kind,
            ctx.flow_run_id,
            ctx.from_state,
            ctx.to_state,
        )
        return None
    return raw


def emit_flow_transition(
    specs: tuple[TransitionHookSpec, ...] | None,
    flow_run_id: UUID,
    from_state: RunState,
    to_state: RunState,
    transition_kind: str,
    metadata: dict[str, Any] | None = None,
    *,
    proposed_to_state: RunState | None = None,
    exception: BaseException | None = None,
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
        proposed_to_state=proposed_to_state,
        exception=exception,
    )
    dispatch_transition_hooks(specs, ctx)


def emit_flow_hooks_for_batch(
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
        emit_flow_transition(specs, flow_run_id, prev, to_state, kind)
        prev = res.state


def emit_task_transition_edges(
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


def emit_task_single_hook_edge(
    specs: tuple[TransitionHookSpec, ...],
    task_run: TaskRunRecord,
    task_name: str,
    from_state: RunState,
    to_state: RunState,
    event_type: str,
    metadata: dict[str, Any] | None = None,
    *,
    proposed_to_state: RunState | None = None,
    exception: BaseException | None = None,
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
        proposed_to_state=proposed_to_state,
        exception=exception,
    )
    dispatch_transition_hooks(specs, ctx)
