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


@dataclass(frozen=True)
class RewriteProbe:
    """Result of a pre-commit rewrite scan (first legal returned state wins)."""

    decision: TransitionDecision | None
    invoked_ids: frozenset[int]
    winner_id: int | None


def on_transition(
    fn: Callable[[TransitionContext], Any],
    *,
    from_state: RunState | None = None,
    to_state: RunState | None = None,
) -> TransitionHookSpec:
    """Register ``fn`` for edges matching optional ``from_state`` / ``to_state``.

    Returning ``None`` observes the committed edge (post-commit). Returning a
    ``RunState`` or ``TransitionDecision`` on a proposed ``RUNNING`` → terminal
    edge rewrites the destination before the FSM apply.
    """
    return TransitionHookSpec(fn=fn, from_state=from_state, to_state=to_state)


def compile_transition_hooks(
    specs: Sequence[TransitionHookSpec] | None,
) -> tuple[TransitionHookSpec, ...] | None:
    """Freeze hook registration order for dispatch; returns ``None`` when there is nothing to run."""
    if not specs:
        return None
    return tuple(specs)


def _edge_matches(spec: TransitionHookSpec, ctx: TransitionContext) -> bool:
    from_ok = spec.from_state is None or spec.from_state == ctx.from_state
    to_ok = spec.to_state is None or spec.to_state == ctx.to_state
    return from_ok and to_ok


def dispatch_transition_hooks(
    specs: tuple[TransitionHookSpec, ...] | None,
    ctx: TransitionContext,
    *,
    skip_ids: frozenset[int] | None = None,
) -> None:
    """Run matching hooks in registration order. Exceptions are logged and swallowed.

    Return values are ignored (observe). Use ``resolve_terminal_rewrite`` for
    pre-commit destination overrides.
    """
    if not specs:
        return
    skip = skip_ids or frozenset()
    for spec in specs:
        if id(spec) in skip:
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
) -> RewriteProbe:
    """Call matching hooks on the proposed edge; first legal returned state wins.

    Later specs are not called once a rewrite is chosen (no cascade). Exceptions
    and illegal returns are logged and skipped.
    """
    if not specs:
        return RewriteProbe(decision=None, invoked_ids=frozenset(), winner_id=None)
    if ctx.from_state != RunState.RUNNING or ctx.to_state not in REWRITE_TERMINALS:
        return RewriteProbe(decision=None, invoked_ids=frozenset(), winner_id=None)
    invoked: set[int] = set()
    for spec in specs:
        if not _edge_matches(spec, ctx):
            continue
        spec_id = id(spec)
        invoked.add(spec_id)
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
        return RewriteProbe(
            decision=decision, invoked_ids=frozenset(invoked), winner_id=spec_id
        )
    return RewriteProbe(decision=None, invoked_ids=frozenset(invoked), winner_id=None)


def _normalize_rewrite_decision(
    raw: Any, ctx: TransitionContext
) -> TransitionDecision | None:
    if isinstance(raw, RunState):
        raw = TransitionDecision(to_state=raw)
    elif not isinstance(raw, TransitionDecision):
        logger.warning(
            "transition rewrite ignored (expected RunState or TransitionDecision) "
            "kind=%s flow_run_id=%s from=%s to=%s got=%s",
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
    skip_ids: frozenset[int] | None = None,
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
    dispatch_transition_hooks(specs, ctx, skip_ids=skip_ids)


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
    skip_ids: frozenset[int] | None = None,
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
    dispatch_transition_hooks(specs, ctx, skip_ids=skip_ids)
