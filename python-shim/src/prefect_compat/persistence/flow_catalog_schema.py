"""Additive flow-catalog tables, columns, and DISTINCT-name backfill."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def _column_names_sqlite(conn: Any, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names: set[str] = set()
    for row in rows:
        if hasattr(row, "keys") and "name" in row.keys():
            names.add(str(row["name"]))
        else:
            names.add(str(row[1]))
    return names


def _add_column_sqlite(conn: Any, table: str, column: str, decl: str) -> None:
    if column not in _column_names_sqlite(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def upgrade_flow_catalog_sqlite(conn: Any) -> None:
    _add_column_sqlite(conn, "flow_runs", "flow_id", "TEXT")
    _add_column_sqlite(conn, "deployments", "flow_id", "TEXT")
    _add_column_sqlite(conn, "deployments", "deleted_at", "TEXT")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS flows (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT,
            deleted_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_flows_name ON flows(name);
        CREATE TABLE IF NOT EXISTS flow_aliases (
            name TEXT PRIMARY KEY,
            flow_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_flow_aliases_flow ON flow_aliases(flow_id);
        CREATE INDEX IF NOT EXISTS idx_flow_runs_flow_id ON flow_runs(flow_id);
        CREATE INDEX IF NOT EXISTS idx_deployments_flow_id ON deployments(flow_id);
        CREATE INDEX IF NOT EXISTS idx_flow_runs_updated_state ON flow_runs(updated_at, state);
        CREATE INDEX IF NOT EXISTS idx_flows_status_updated ON flows(status, updated_at);
        """
    )
    backfill_flow_catalog(conn)


def upgrade_flow_catalog_postgres(cur: Any) -> None:
    cur.execute("ALTER TABLE flow_runs ADD COLUMN IF NOT EXISTS flow_id TEXT")
    cur.execute("ALTER TABLE deployments ADD COLUMN IF NOT EXISTS flow_id TEXT")
    cur.execute("ALTER TABLE deployments ADD COLUMN IF NOT EXISTS deleted_at TEXT")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS flows (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT,
            deleted_at TEXT
        )
        """
    )
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_flows_name ON flows(name)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS flow_aliases (
            name TEXT PRIMARY KEY,
            flow_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_flow_aliases_flow ON flow_aliases(flow_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_flow_runs_flow_id ON flow_runs(flow_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_deployments_flow_id ON deployments(flow_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_flow_runs_updated_state ON flow_runs(updated_at, state)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_flows_status_updated ON flows(status, updated_at)"
    )
    backfill_flow_catalog(cur)


def _fetch_all(conn: Any, sql: str, params: list[Any] | None = None) -> list[Any]:
    if params is None:
        cur = conn.execute(sql)
    else:
        cur = conn.execute(sql, params)
    if hasattr(cur, "fetchall"):
        return list(cur.fetchall())
    return []


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    return row[index]


def backfill_flow_catalog(conn: Any) -> None:
    names: set[str] = set()
    for row in _fetch_all(conn, "SELECT DISTINCT name FROM flow_runs"):
        value = _row_value(row, "name", 0)
        if value:
            names.add(str(value))
    for row in _fetch_all(conn, "SELECT DISTINCT flow_name FROM deployments"):
        value = _row_value(row, "flow_name", 0)
        if value:
            names.add(str(value))
    existing: set[str] = set()
    for row in _fetch_all(conn, "SELECT name FROM flows"):
        value = _row_value(row, "name", 0)
        if value:
            existing.add(str(value))
    now = datetime.now(UTC).isoformat()
    for name in sorted(names):
        if name in existing:
            continue
        conn.execute(
            "INSERT INTO flows(id,name,status,created_at,updated_at) VALUES(?,?,?,?,?)",
            [str(uuid4()), name, "active", now, now],
        )
    conn.execute(
        """
        UPDATE flow_runs
        SET flow_id = (SELECT id FROM flows WHERE flows.name = flow_runs.name)
        WHERE flow_id IS NULL
        """
    )
    conn.execute(
        """
        UPDATE deployments
        SET flow_id = (SELECT id FROM flows WHERE flows.name = deployments.flow_name)
        WHERE flow_id IS NULL
        """
    )
