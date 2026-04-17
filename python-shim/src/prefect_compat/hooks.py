"""Unified (from_state, to_state) transition hooks for flows and tasks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal, Sequence
from uuid import UUID

from .runtime import RunState

logger = logging.getLogger(__name__)

TransitionKind = Literal["flow", "task"]


@dataclass
class TransitionContext:
    """Immutable facts about a single applied control-plane transition (post-commit)."""

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


@dataclass(frozen=True)
class TransitionHookSpec:
    fn: Callable[[TransitionContext], None]
    from_state: RunState | None = None
    to_state: RunState | None = None


def on_transition(
    fn: Callable[[TransitionContext], None],
    *,
    from_state: RunState | None = None,
    to_state: RunState | None = None,
) -> TransitionHookSpec:
    """Register ``fn`` for edges matching optional ``from_state`` / ``to_state`` (``None`` = wildcard)."""
    return TransitionHookSpec(fn=fn, from_state=from_state, to_state=to_state)


def compile_transition_hooks(
    specs: Sequence[TransitionHookSpec] | None,
) -> tuple[TransitionHookSpec, ...] | None:
    """Freeze hook registration order for dispatch; returns ``None`` when there is nothing to run."""
    if not specs:
        return None
    return tuple(specs)


def dispatch_transition_hooks(
    specs: tuple[TransitionHookSpec, ...] | None, ctx: TransitionContext
) -> None:
    """Run matching hooks in registration order. Exceptions in user hooks are logged and swallowed."""
    if not specs:
        return
    for spec in specs:
        from_ok = spec.from_state is None or spec.from_state == ctx.from_state
        to_ok = spec.to_state is None or spec.to_state == ctx.to_state
        if not (from_ok and to_ok):
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
