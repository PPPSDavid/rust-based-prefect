"""Control-plane persistence backends (SQLite default; Postgres via URL)."""

from __future__ import annotations

from typing import Any

from .constants import DEFAULT_WORK_POOL_ID
from .factory import create_store, resolve_sqlite_path
from .protocol import ControlPlaneStore
from .store_sqlite import SqliteStore

__all__ = [
    "ControlPlaneStore",
    "DEFAULT_WORK_POOL_ID",
    "PostgresStore",
    "SqliteStore",
    "create_store",
    "resolve_sqlite_path",
]


def __getattr__(name: str) -> Any:
    if name == "PostgresStore":
        from .store_postgres import PostgresStore

        return PostgresStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
