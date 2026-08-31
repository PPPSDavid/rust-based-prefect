"""Shared mixin base so the type checker accepts cross-mixin `self.*` access."""

from __future__ import annotations

from typing import Any


class ControlPlaneMixinBase:
    """Implemented by ``InMemoryControlPlane`` (composition of mixins)."""

    def __getattr__(self, item: str) -> Any:  # pragma: no cover - typing aid
        raise AttributeError(item)
