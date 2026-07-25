"""Flow-run lifecycle interrupt modes (P3.2).

Cancel always terminates. Operator pause requires an explicit mode — there is
no ambiguous single ``pause`` default.
"""

from __future__ import annotations

from enum import StrEnum


class InterruptMode(StrEnum):
    """How an operator interrupt treats in-flight RUNNING task bodies."""

    DRAIN = "drain"
    """Block new work; let current RUNNING tasks finish; then hold ``PAUSED``."""

    TERMINATE = "terminate"
    """Stop in-flight work ASAP (process-isolated kill when available); hold ``PAUSED``."""


def parse_interrupt_mode(value: str | InterruptMode | None) -> InterruptMode:
    """Parse a required interrupt mode; raises ``ValueError`` if missing/invalid."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("mode is required: 'drain' or 'terminate'")
    if isinstance(value, InterruptMode):
        return value
    normalized = str(value).strip().lower()
    try:
        return InterruptMode(normalized)
    except ValueError as exc:
        raise ValueError("mode must be 'drain' or 'terminate'") from exc


def parse_cancel_mode(value: str | InterruptMode | None) -> InterruptMode:
    """Cancel accepts only ``terminate`` (default when omitted)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return InterruptMode.TERMINATE
    mode = parse_interrupt_mode(value)
    if mode != InterruptMode.TERMINATE:
        raise ValueError("cancel only supports mode='terminate' in v1")
    return mode
