"""Flow-run list filters, sort, and keyset cursors (Python fallback parity for Rust)."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

_DEFAULT_SORT = "seq"
_DEFAULT_ORDER = "desc"
_CURSOR_PREFIX = "v1."
_SORT_FIELDS = frozenset({"seq", "created_at", "updated_at", "name", "state"})
_ORDERS = frozenset({"asc", "desc"})
_PLAIN_SEQ = re.compile(r"^-?\d+$")


@dataclass(frozen=True)
class CursorToken:
    field: str
    order: str
    key: str | None
    seq: int


def normalize_sort(raw: str | None) -> str:
    sort = (raw or _DEFAULT_SORT).strip().lower()
    if sort not in _SORT_FIELDS:
        raise ValueError(
            f"invalid sort {raw!r}; expected seq|created_at|updated_at|name|state"
        )
    return sort


def normalize_order(raw: str | None) -> str:
    order = (raw or _DEFAULT_ORDER).strip().lower()
    if order not in _ORDERS:
        raise ValueError(f"invalid order {raw!r}; expected asc|desc")
    return order


def encode_cursor(token: CursorToken) -> str:
    if (
        token.field == _DEFAULT_SORT
        and token.order == _DEFAULT_ORDER
        and token.key is None
    ):
        return str(token.seq)
    payload = json.dumps(
        {"f": token.field, "o": token.order, "k": token.key, "s": token.seq},
        separators=(",", ":"),
    )
    encoded = (
        base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    )
    return f"{_CURSOR_PREFIX}{encoded}"


def decode_cursor(raw: str, sort: str, order: str) -> CursorToken:
    if _PLAIN_SEQ.fullmatch(raw):
        if sort != _DEFAULT_SORT or order != _DEFAULT_ORDER:
            raise ValueError(
                "plain seq cursor is only valid with default sort=seq&order=desc; "
                "clear cursor when changing sort"
            )
        return CursorToken(
            field=_DEFAULT_SORT, order=_DEFAULT_ORDER, key=None, seq=int(raw)
        )
    if not raw.startswith(_CURSOR_PREFIX):
        raise ValueError("invalid cursor encoding")
    encoded = raw[len(_CURSOR_PREFIX) :]
    pad = "=" * (-len(encoded) % 4)
    try:
        raw_json = base64.urlsafe_b64decode(encoded + pad)
        value = json.loads(raw_json.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid cursor: {exc}") from exc
    field = value.get("f")
    cursor_order = value.get("o")
    seq = value.get("s")
    if (
        not isinstance(field, str)
        or not isinstance(cursor_order, str)
        or not isinstance(seq, int)
    ):
        raise ValueError("cursor missing f/o/s")
    if field != sort or cursor_order != order:
        raise ValueError(
            "cursor sort/order does not match request; clear cursor when changing sort"
        )
    key = value.get("k")
    if key is not None and not isinstance(key, str):
        raise ValueError("cursor key must be string or null")
    return CursorToken(field=field, order=cursor_order, key=key, seq=seq)


def like_pattern(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def sort_column(sort: str) -> str:
    return {
        "created_at": "fr.created_at",
        "updated_at": "fr.updated_at",
        "name": "fr.name",
        "state": "fr.state",
        "seq": "fr.seq",
    }[sort]


def build_flow_run_list_sql(
    *,
    state: str | None,
    flow_name: str | None,
    deployment_id: str | None,
    created_after: str | None,
    created_before: str | None,
    q: str | None,
    sort: str,
    order: str,
    cursor: str | None,
    limit: int,
    hide_archived: bool,
) -> tuple[str, list[Any], CursorToken | None]:
    """Return (sql, params, decoded_cursor). Raises ValueError on bad cursor/sort."""
    sort_n = normalize_sort(sort)
    order_n = normalize_order(order)
    token = decode_cursor(cursor, sort_n, order_n) if cursor else None

    sql = (
        "SELECT fr.seq,fr.id,fr.name,fr.state,fr.version,fr.created_at,fr.updated_at,"
        "fr.parent_flow_run_id,fr.parent_task_run_id,fr.root_flow_run_id,"
        "fr.execution_mode,fr.depth,fr.flow_id FROM flow_runs fr "
        "LEFT JOIN flows catalog ON catalog.id = fr.flow_id"
    )
    conditions: list[str] = []
    params: list[Any] = []
    if state:
        conditions.append("fr.state = ?")
        params.append(state)
    if flow_name:
        conditions.append("fr.name = ?")
        params.append(flow_name)
    if q:
        conditions.append("fr.name LIKE ? ESCAPE '\\'")
        params.append(like_pattern(q))
    if created_after:
        conditions.append("fr.created_at >= ?")
        params.append(created_after)
    if created_before:
        conditions.append("fr.created_at <= ?")
        params.append(created_before)
    if deployment_id:
        conditions.append(
            "EXISTS (SELECT 1 FROM deployment_runs dr "
            "WHERE dr.flow_run_id = fr.id AND dr.deployment_id = ?)"
        )
        params.append(deployment_id)
    if hide_archived:
        conditions.append("(catalog.id IS NULL OR catalog.status = 'active')")
    else:
        conditions.append(
            "(catalog.id IS NULL OR catalog.status IN ('active','archived'))"
        )

    if token is not None:
        col = sort_column(sort_n)
        if sort_n == _DEFAULT_SORT:
            conditions.append(f"fr.seq {'<' if order_n == 'desc' else '>'} ?")
            params.append(token.seq)
        else:
            op = "<" if order_n == "desc" else ">"
            conditions.append(f"({col} {op} ? OR ({col} = ? AND fr.seq {op} ?))")
            if token.key is None:
                raise ValueError("cursor missing sort key")
            params.extend([token.key, token.key, token.seq])

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    if sort_n == _DEFAULT_SORT:
        sql += f" ORDER BY fr.seq {order_n.upper()} LIMIT ?"
    else:
        col = sort_column(sort_n)
        sql += f" ORDER BY {col} {order_n.upper()}, fr.seq {order_n.upper()} LIMIT ?"
    params.append(limit)
    return sql, params, token


def next_cursor_for_page(
    rows: list[Any],
    *,
    limit: int,
    sort: str,
    order: str,
) -> str | None:
    if len(rows) != limit:
        return None
    last = rows[-1]
    sort_n = normalize_sort(sort)
    order_n = normalize_order(order)
    seq = int(last["seq"])
    key = None if sort_n == _DEFAULT_SORT else str(last[sort_n])
    return encode_cursor(CursorToken(field=sort_n, order=order_n, key=key, seq=seq))
