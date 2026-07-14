"""Control-plane persistence protocol (Tier B0).

Backends implement a thin connection/schema surface today. Query and mutation
APIs remain on ``InMemoryControlPlane`` until Postgres (B1) migrates call sites.
Hot-path claim/schedule/FSM remains in ``rust-engine``; see
``docs/plans/self-hosted-storage-rfc.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ControlPlaneStore(Protocol):
    """Persistence backend bound to a control plane instance."""

    @property
    def backend_kind(self) -> str:
        """``sqlite`` or ``postgres`` (future)."""
        ...

    @property
    def path(self) -> Path | None:
        """Filesystem path for SQLite / Rust ``bind_db``; None for network DBs."""
        ...

    @property
    def connection(self) -> Any:
        """Backend connection object (``sqlite3.Connection`` for SQLite)."""
        ...

    def ensure_schema(self) -> None:
        """Create tables and apply additive upgrades (idempotent)."""
        ...

    def close(self) -> None:
        """Release backend resources."""
        ...
