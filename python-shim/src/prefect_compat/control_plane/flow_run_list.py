"""Flow-run list filters shared by the HTTP route and the Python query fallback.

The Rust reader (`flow_run_list::query_flow_runs`) is the hot path. This module
keeps the same contract when the native bridge is unavailable, and it normalizes
filter values before either path runs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

FLOW_RUN_STATES = frozenset(
    {
        "SCHEDULED",
        "PENDING",
        "RUNNING",
        "PAUSED",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }
)

RowFetch = Callable[[str, list[Any]], list[Any]]

_LATEST_DEPLOYMENT = """
LEFT JOIN (
    SELECT dr.flow_run_id, dr.deployment_id, dr.started_at, dr.finished_at
    FROM deployment_runs dr
    INNER JOIN (
        SELECT flow_run_id, MAX(seq) AS seq
        FROM deployment_runs
        WHERE flow_run_id IS NOT NULL
        GROUP BY flow_run_id
    ) pick ON pick.flow_run_id = dr.flow_run_id AND pick.seq = dr.seq
) latest ON latest.flow_run_id = fr.id
LEFT JOIN deployments dep ON dep.id = latest.deployment_id
"""


@dataclass(frozen=True)
class FlowRunListFilters:
    state: str | None
    q: str | None
    like: str | None
    created_after: str | None
    created_before: str | None
    limit: int
    cursor: str | None
    cursor_seq: int | None


def prepare_flow_run_list_filters(
    *,
    state: str | None = None,
    q: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> FlowRunListFilters:
    """Normalize list filters. Raises ``ValueError`` on a bad timestamp or cursor."""
    cleaned_state = (state or "").strip() or None
    cleaned_q = (q or "").strip() or None
    if cleaned_q is not None and len(cleaned_q) > 200:
        raise ValueError("q must be at most 200 characters")
    cursor_text = (cursor or "").strip() or None
    cursor_seq: int | None = None
    if cursor_text is not None:
        try:
            cursor_seq = int(cursor_text)
        except ValueError as exc:
            raise ValueError("cursor must be an integer seq") from exc
    return FlowRunListFilters(
        state=cleaned_state,
        q=cleaned_q,
        like=like_contains(cleaned_q) if cleaned_q else None,
        created_after=normalize_timestamp(created_after, "created_after"),
        created_before=normalize_timestamp(created_before, "created_before"),
        limit=limit,
        cursor=cursor_text,
        cursor_seq=cursor_seq,
    )


def normalize_timestamp(value: str | None, name: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    parsed_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(parsed_text)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def like_contains(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped.lower()}%"


def encode_flow_tags(tags: Sequence[str] | None) -> str | None:
    if not tags:
        return None
    cleaned = [str(item) for item in tags if str(item).strip()]
    if not cleaned:
        return None
    return json.dumps(cleaned)


def stamp_flow_run_tags(conn: Any, run_id: str, tags: Sequence[str] | None) -> None:
    encoded = encode_flow_tags(tags)
    if encoded is None:
        return
    conn.execute(
        "UPDATE flow_runs SET tags = ? WHERE id = ?",
        [encoded, run_id],
    )


def parse_tags(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def query_flow_runs_page(
    fetch: RowFetch,
    filters: FlowRunListFilters,
    *,
    hide_archived: bool,
) -> tuple[list[dict[str, Any]], str | None]:
    """Return ``(items, next_cursor)`` for the Python SQLite/Postgres fallback."""
    sql, params = _build_query(filters, hide_archived=hide_archived)
    rows = fetch(sql, params)
    has_more = len(rows) > filters.limit
    page_rows = rows[: filters.limit]
    items = [_row_to_item(row) for row in page_rows]
    next_cursor = str(page_rows[-1]["seq"]) if has_more and page_rows else None
    return items, next_cursor


def _build_query(
    filters: FlowRunListFilters, *, hide_archived: bool
) -> tuple[str, list[Any]]:
    sql = (
        "SELECT fr.seq, fr.id, fr.name, fr.state, fr.version, fr.created_at, fr.updated_at, "
        "fr.parent_flow_run_id, fr.parent_task_run_id, fr.root_flow_run_id, "
        "fr.execution_mode, fr.depth, fr.flow_id, "
        "COALESCE(catalog.name, fr.name) AS flow_name, fr.tags, "
        "latest.deployment_id AS deployment_id, dep.name AS deployment_name, "
        "latest.started_at AS start_time, latest.finished_at AS end_time "
        "FROM flow_runs fr "
        "LEFT JOIN flows catalog ON catalog.id = fr.flow_id "
        f"{_LATEST_DEPLOYMENT}"
    )
    conditions: list[str] = []
    params: list[Any] = []
    if filters.state:
        conditions.append("fr.state = ?")
        params.append(filters.state)
    if hide_archived:
        conditions.append("(catalog.id IS NULL OR catalog.status = 'active')")
    else:
        conditions.append(
            "(catalog.id IS NULL OR catalog.status IN ('active','archived'))"
        )
    if filters.like is not None:
        conditions.append(
            "("
            "LOWER(fr.name) LIKE ? ESCAPE '\\' OR "
            "LOWER(COALESCE(catalog.name, fr.name)) LIKE ? ESCAPE '\\' OR "
            "LOWER(COALESCE(dep.name, '')) LIKE ? ESCAPE '\\' OR "
            "LOWER(COALESCE(fr.tags, '')) LIKE ? ESCAPE '\\'"
            ")"
        )
        params.extend([filters.like, filters.like, filters.like, filters.like])
    if filters.created_after is not None:
        conditions.append("fr.created_at >= ?")
        params.append(filters.created_after)
    if filters.created_before is not None:
        conditions.append("fr.created_at < ?")
        params.append(filters.created_before)
    if filters.cursor_seq is not None:
        conditions.append("fr.seq < ?")
        params.append(filters.cursor_seq)
    sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY fr.seq DESC LIMIT ?"
    params.append(filters.limit + 1)
    return sql, params


def _row_to_item(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "state": row["state"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "parent_flow_run_id": row["parent_flow_run_id"],
        "parent_task_run_id": row["parent_task_run_id"],
        "root_flow_run_id": row["root_flow_run_id"],
        "execution_mode": row["execution_mode"],
        "depth": row["depth"] if row["depth"] is not None else 0,
        "flow_id": row["flow_id"],
        "flow_name": row["flow_name"],
        "deployment_id": row["deployment_id"],
        "deployment_name": row["deployment_name"],
        "tags": parse_tags(row["tags"]),
        "start_time": row["start_time"],
        "end_time": row["end_time"],
    }
