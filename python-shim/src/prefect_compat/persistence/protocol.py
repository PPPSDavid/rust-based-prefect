"""Control-plane persistence protocol (Tier B0/B1).

Backends implement a thin connection/schema surface. Query and mutation
SQL mostly remains on ``InMemoryControlPlane`` with a sqlite-shaped
execute API (Postgres goes through ``PostgresConnectionAdapter``).
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
        """``sqlite`` or ``postgres``."""
        ...

    @property
    def path(self) -> Path | None:
        """Filesystem path for SQLite / Rust ``bind_db``; None for network DBs."""
        ...

    @property
    def connection(self) -> Any:
        """Backend connection (``sqlite3.Connection`` or adapter)."""
        ...

    def ensure_schema(self) -> None:
        """Create tables and apply additive upgrades (idempotent)."""
        ...

    def close(self) -> None:
        """Release backend resources."""
        ...
