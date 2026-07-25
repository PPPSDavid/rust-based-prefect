"""Rewrite SQLite-shaped SQL for Postgres (placeholder + upsert + rowid)."""

from __future__ import annotations

import re
from typing import Any


_INSERT_OR_IGNORE = re.compile(
    r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+",
    re.IGNORECASE,
)
_INSERT_OR_REPLACE = re.compile(
    r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*",
    re.IGNORECASE | re.DOTALL,
)


def qmark_to_pyformat(sql: str) -> str:
    """Convert sqlite ``?`` placeholders to psycopg ``%s`` (outside quotes)."""
    out: list[str] = []
    i = 0
    in_single = False
    in_double = False
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            i += 1
            continue
        if ch == "?" and not in_single and not in_double:
            out.append("%s")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def rewrite_sqlite_sql_for_postgres(sql: str) -> str:
    """Best-effort dialect rewrite for FlowOxide control-plane SQL."""
    text = sql.strip()
    # Pagination / listing used SQLite rowid; Postgres schema exposes seq.
    text = re.sub(r"\browid\b", "seq", text, flags=re.IGNORECASE)

    if _INSERT_OR_IGNORE.match(text):
        text = _INSERT_OR_IGNORE.sub("INSERT INTO ", text, count=1)
        text = text.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    m = _INSERT_OR_REPLACE.match(text)
    if m:
        table = m.group(1)
        # dag_manifests is the only OR REPLACE site today (UNIQUE flow_run_id).
        if table.lower() == "dag_manifests":
            text = re.sub(
                r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+dag_manifests\s*",
                "INSERT INTO dag_manifests ",
                text,
                count=1,
                flags=re.IGNORECASE,
            )
            text = (
                text.rstrip().rstrip(";")
                + " ON CONFLICT (flow_run_id) DO UPDATE SET "
                + "manifest_json = EXCLUDED.manifest_json, "
                + "forecast_json = EXCLUDED.forecast_json, "
                + "warnings_json = EXCLUDED.warnings_json, "
                + "fallback_required = EXCLUDED.fallback_required, "
                + "source = EXCLUDED.source, "
                + "updated_at = EXCLUDED.updated_at"
            )
        else:
            raise NotImplementedError(
                f"INSERT OR REPLACE rewrite not defined for table {table!r}"
            )

    # SQLite ON CONFLICT ... excluded.* → Postgres EXCLUDED.*
    text = re.sub(r"\bexcluded\.", "EXCLUDED.", text, flags=re.IGNORECASE)

    return qmark_to_pyformat(text)


class PostgresCursor:
    """Minimal sqlite3.Cursor-compatible wrapper around a psycopg cursor."""

    def __init__(self, rows: list[Any], rowcount: int) -> None:
        self._rows = rows
        self.rowcount = rowcount
        self._idx = 0

    def fetchall(self) -> list[Any]:
        rows = self._rows[self._idx :]
        self._idx = len(self._rows)
        return rows

    def fetchone(self) -> Any | None:
        if self._idx >= len(self._rows):
            return None
        row = self._rows[self._idx]
        self._idx += 1
        return row


class PostgresConnectionAdapter:
    """sqlite3-like facade over a psycopg connection (autocommit)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, parameters: Any = ()) -> PostgresCursor:
        rewritten = rewrite_sqlite_sql_for_postgres(sql)
        params = tuple(parameters) if parameters is not None else ()
        with self._conn.cursor() as cur:
            cur.execute(rewritten, params)
            rows: list[Any] = []
            if cur.description is not None:
                rows = list(cur.fetchall())
            return PostgresCursor(rows, cur.rowcount)

    def close(self) -> None:
        self._conn.close()
