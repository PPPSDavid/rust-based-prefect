"""Store factory for control-plane persistence."""

from __future__ import annotations

import os
from pathlib import Path

from .protocol import ControlPlaneStore
from .store_sqlite import SqliteStore


def resolve_sqlite_path(history_path: str | Path | None = None) -> Path:
    """Derive SQLite path from history JSONL path (same rules as runtime)."""
    if history_path:
        return Path(history_path).with_suffix(".db")
    return Path("data") / "ironflow_ui.db"


def create_store(
    *,
    history_path: str | Path | None = None,
    database_url: str | None = None,
    sqlite_path: str | Path | None = None,
) -> ControlPlaneStore:
    """Create a control-plane store.

    ``IRONFLOW_DATABASE_URL`` / ``database_url`` with a Postgres DSN selects
    ``PostgresStore``; otherwise opens SQLite (local/dev default).
    """
    url = (
        database_url
        if database_url is not None
        else os.getenv("IRONFLOW_DATABASE_URL", "")
    ).strip()
    if url and url.lower().startswith(("postgres://", "postgresql://")):
        # Import only when selected so envs without psycopg can use SQLite.
        from .store_postgres import PostgresStore

        return PostgresStore.open(url)
    if url and not url.lower().startswith("sqlite"):
        raise ValueError(
            f"Unsupported IRONFLOW_DATABASE_URL scheme (expected sqlite or postgres): {url!r}"
        )

    path = Path(sqlite_path) if sqlite_path else resolve_sqlite_path(history_path)
    return SqliteStore.open(path)
