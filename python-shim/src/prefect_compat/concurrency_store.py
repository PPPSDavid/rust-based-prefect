"""SQLite fallback for global concurrency limit (GCL) ops when Rust bind_db is unavailable.

Semantics mirror ``rust-engine/src/concurrency_ops.rs``. Prefer the Rust path in
production; this module exists for tests/environments without the native library.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any


def ensure_schema(conn: Any) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS concurrency_limits (
            id TEXT PRIMARY KEY NOT NULL,
            name TEXT NOT NULL UNIQUE,
            limit_slots INTEGER NOT NULL,
            active_slots INTEGER NOT NULL DEFAULT 0,
            slot_decay_per_second REAL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS concurrency_leases (
            id TEXT PRIMARY KEY NOT NULL,
            limit_id TEXT NOT NULL,
            occupy INTEGER NOT NULL,
            holder_type TEXT,
            holder_id TEXT,
            acquired_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            mode TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_concurrency_limits_name
            ON concurrency_limits(name);
        CREATE INDEX IF NOT EXISTS idx_concurrency_leases_expires
            ON concurrency_leases(expires_at);
        CREATE INDEX IF NOT EXISTS idx_concurrency_leases_limit
            ON concurrency_leases(limit_id);
        """
    )


def _parse_now(now: str | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    return datetime.fromisoformat(now.replace("Z", "+00:00")).astimezone(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _row_to_limit(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "limit": int(row["limit_slots"]),
        "active_slots": int(row["active_slots"]),
        "slot_decay_per_second": row["slot_decay_per_second"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def upsert_limit(conn: Any, body: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    name = str(body["name"]).strip()
    if not name:
        raise ValueError("name must be non-empty")
    limit = int(body["limit"])
    if limit < 0:
        raise ValueError("limit must be >= 0")
    decay = body.get("slot_decay_per_second")
    if decay is not None:
        decay = float(decay)
        if decay <= 0:
            raise ValueError("slot_decay_per_second must be > 0")
    active = bool(body.get("active", True))
    now = _iso(_parse_now(body.get("now")))
    existing = conn.execute(
        "SELECT id FROM concurrency_limits WHERE name = ?", [name]
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE concurrency_limits SET limit_slots = ?, slot_decay_per_second = ?, "
            "active = ?, updated_at = ? WHERE id = ?",
            [limit, decay, 1 if active else 0, now, existing["id"]],
        )
    else:
        lim_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO concurrency_limits(id, name, limit_slots, active_slots, "
            "slot_decay_per_second, active, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [lim_id, name, limit, 0, decay, 1 if active else 0, now, now],
        )
    row = conn.execute(
        "SELECT id, name, limit_slots, active_slots, slot_decay_per_second, active, "
        "created_at, updated_at FROM concurrency_limits WHERE name = ?",
        [name],
    ).fetchone()
    return _row_to_limit(row)


def delete_limit(conn: Any, name: str) -> dict[str, Any]:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT id FROM concurrency_limits WHERE name = ?", [name]
    ).fetchone()
    if not row:
        return {"ok": True, "deleted": False}
    conn.execute("DELETE FROM concurrency_leases WHERE limit_id = ?", [row["id"]])
    conn.execute("DELETE FROM concurrency_limits WHERE id = ?", [row["id"]])
    return {"ok": True, "deleted": True}


def get_limit(conn: Any, name: str) -> dict[str, Any]:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT id, name, limit_slots, active_slots, slot_decay_per_second, active, "
        "created_at, updated_at FROM concurrency_limits WHERE name = ?",
        [name],
    ).fetchone()
    return {"ok": True, "limit": _row_to_limit(row) if row else None}


def list_limits(conn: Any) -> dict[str, Any]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT id, name, limit_slots, active_slots, slot_decay_per_second, active, "
        "created_at, updated_at FROM concurrency_limits ORDER BY name ASC"
    ).fetchall()
    return {"ok": True, "limits": [_row_to_limit(r) for r in rows]}


def reclaim_expired(conn: Any, now: str | None = None) -> int:
    ensure_schema(conn)
    now_s = _iso(_parse_now(now))
    expired = conn.execute(
        "SELECT id, limit_id, occupy FROM concurrency_leases WHERE expires_at <= ?",
        [now_s],
    ).fetchall()
    by_limit: dict[str, int] = {}
    for row in expired:
        conn.execute("DELETE FROM concurrency_leases WHERE id = ?", [row["id"]])
        by_limit[row["limit_id"]] = by_limit.get(row["limit_id"], 0) + int(row["occupy"])
    for limit_id, freed in by_limit.items():
        conn.execute(
            "UPDATE concurrency_limits SET active_slots = CASE "
            "WHEN active_slots > ? THEN active_slots - ? ELSE 0 END, "
            "updated_at = ? WHERE id = ?",
            [freed, freed, now_s, limit_id],
        )
    return len(expired)


def acquire(conn: Any, body: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    names_raw = body.get("names")
    if isinstance(names_raw, str):
        names = [names_raw]
    elif isinstance(names_raw, list):
        names = [str(n) for n in names_raw]
    else:
        raise ValueError("names must be a string or array")
    names = sorted(set(names))
    if not names:
        raise ValueError("names must be non-empty")
    occupy = int(body.get("occupy", 1))
    if occupy <= 0:
        raise ValueError("occupy must be > 0")
    mode = str(body.get("mode", "concurrency"))
    if mode not in ("concurrency", "rate_limit"):
        raise ValueError("mode must be concurrency or rate_limit")
    lease_duration = float(body.get("lease_duration", 300))
    lease_duration = max(1.0, lease_duration)
    strict = bool(body.get("strict", False))
    holder_type = body.get("holder_type")
    holder_id = body.get("holder_id")
    now_dt = _parse_now(body.get("now"))
    now_s = _iso(now_dt)

    reclaim_expired(conn, now_s)

    resolved: list[tuple[str, Any]] = []
    for name in names:
        row = conn.execute(
            "SELECT id, name, limit_slots, active_slots, slot_decay_per_second, active "
            "FROM concurrency_limits WHERE name = ?",
            [name],
        ).fetchone()
        resolved.append((name, row))

    effective: list[Any] = []
    for name, row in resolved:
        if row is None:
            if strict:
                return {
                    "ok": False,
                    "status": "missing",
                    "error": {"code": "missing_limit", "name": name},
                }
            continue
        if not row["active"]:
            if strict:
                return {
                    "ok": False,
                    "status": "inactive",
                    "error": {"code": "inactive_limit", "name": name},
                }
            continue
        effective.append(row)

    if not effective:
        return {"ok": True, "status": "bypassed", "lease_ids": [], "leases": []}

    if mode == "rate_limit":
        for row in effective:
            if row["slot_decay_per_second"] is None:
                return {
                    "ok": False,
                    "status": "decay_required",
                    "error": {
                        "code": "decay_required",
                        "name": row["name"],
                        "message": "rate_limit requires slot_decay_per_second on all limits",
                    },
                }

    for row in effective:
        if int(row["limit_slots"]) == 0:
            return {
                "ok": True,
                "status": "denied",
                "name": row["name"],
                "lease_ids": [],
                "leases": [],
            }
        if int(row["active_slots"]) + occupy > int(row["limit_slots"]):
            return {
                "ok": True,
                "status": "would_block",
                "name": row["name"],
                "lease_ids": [],
                "leases": [],
            }

    lease_ids: list[str] = []
    leases: list[dict[str, Any]] = []
    for row in effective:
        lease_id = str(uuid.uuid4())
        if mode == "rate_limit":
            decay = float(row["slot_decay_per_second"])
            secs = occupy / decay
            expires = now_dt + timedelta(milliseconds=int(secs * 1000 + 0.999))
        else:
            expires = now_dt + timedelta(milliseconds=int(lease_duration * 1000))
        expires_s = _iso(expires)
        conn.execute(
            "INSERT INTO concurrency_leases(id, limit_id, occupy, holder_type, holder_id, "
            "acquired_at, expires_at, mode) VALUES(?,?,?,?,?,?,?,?)",
            [
                lease_id,
                row["id"],
                occupy,
                holder_type,
                holder_id,
                now_s,
                expires_s,
                mode,
            ],
        )
        conn.execute(
            "UPDATE concurrency_limits SET active_slots = active_slots + ?, updated_at = ? "
            "WHERE id = ?",
            [occupy, now_s, row["id"]],
        )
        lease_ids.append(lease_id)
        leases.append(
            {
                "lease_id": lease_id,
                "limit_id": row["id"],
                "name": row["name"],
                "occupy": occupy,
                "expires_at": expires_s,
                "mode": mode,
            }
        )
    return {"ok": True, "status": "acquired", "lease_ids": lease_ids, "leases": leases}


def release(conn: Any, body: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    raw = body.get("lease_ids")
    if isinstance(raw, str):
        lease_ids = [raw]
    elif isinstance(raw, list):
        lease_ids = [str(x) for x in raw]
    else:
        raise ValueError("lease_ids must be a string or array")
    now_s = _iso(_parse_now(body.get("now")))
    released = 0
    for lease_id in lease_ids:
        row = conn.execute(
            "SELECT limit_id, occupy FROM concurrency_leases WHERE id = ?",
            [lease_id],
        ).fetchone()
        if row is None:
            continue
        conn.execute("DELETE FROM concurrency_leases WHERE id = ?", [lease_id])
        occupy = int(row["occupy"])
        conn.execute(
            "UPDATE concurrency_limits SET active_slots = CASE "
            "WHEN active_slots > ? THEN active_slots - ? ELSE 0 END, "
            "updated_at = ? WHERE id = ?",
            [occupy, occupy, now_s, row["limit_id"]],
        )
        released += 1
    return {"ok": True, "released": released}


def renew(conn: Any, body: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    raw = body.get("lease_ids")
    if isinstance(raw, str):
        lease_ids = [raw]
    elif isinstance(raw, list):
        lease_ids = [str(x) for x in raw]
    else:
        raise ValueError("lease_ids must be a string or array")
    lease_duration = max(1.0, float(body.get("lease_duration", 300)))
    now_dt = _parse_now(body.get("now"))
    expires_s = _iso(now_dt + timedelta(milliseconds=int(lease_duration * 1000)))
    renewed = 0
    for lease_id in lease_ids:
        cur = conn.execute(
            "UPDATE concurrency_leases SET expires_at = ? WHERE id = ?",
            [expires_s, lease_id],
        )
        renewed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return {"ok": True, "renewed": renewed, "expires_at": expires_s}


def tag_limit_name(tag: str) -> str:
    return f"tag:{tag}"
