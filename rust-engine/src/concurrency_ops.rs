//! Global concurrency limit (GCL) slot ledger — SQLite hot path.
//!
//! Named limits hold a capped number of slots. Acquires are atomic across
//! multiple names (sorted lock order). Leases are the source of truth for
//! occupied slots; `active_slots` is maintained in the same transaction.
//! Tag-based limits use the same table with names `tag:{tag}`.

use std::collections::HashMap;

use chrono::{DateTime, Duration, Utc};
use rusqlite::{params, Connection, OptionalExtension, Transaction};
use serde_json::{json, Value};
use uuid::Uuid;

fn parse_now(now_iso: Option<&str>) -> Result<DateTime<Utc>, String> {
    match now_iso {
        Some(s) => DateTime::parse_from_rfc3339(s)
            .map(|dt| dt.with_timezone(&Utc))
            .map_err(|e| format!("invalid now: {e}")),
        None => Ok(Utc::now()),
    }
}

fn ensure_schema(conn: &Connection) -> Result<(), String> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS concurrency_limits (
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
        CREATE INDEX IF NOT EXISTS idx_concurrency_leases_holder
            ON concurrency_leases(holder_id);
        ",
    )
    .map_err(|e| e.to_string())
}

fn limit_row_to_json(row: &rusqlite::Row<'_>) -> Result<Value, rusqlite::Error> {
    Ok(json!({
        "id": row.get::<_, String>(0)?,
        "name": row.get::<_, String>(1)?,
        "limit": row.get::<_, i64>(2)?,
        "active_slots": row.get::<_, i64>(3)?,
        "slot_decay_per_second": row.get::<_, Option<f64>>(4)?,
        "active": row.get::<_, i64>(5)? != 0,
        "created_at": row.get::<_, String>(6)?,
        "updated_at": row.get::<_, String>(7)?,
    }))
}

fn select_limit_by_name(conn: &Connection, name: &str) -> Result<Option<Value>, String> {
    conn.query_row(
        "SELECT id, name, limit_slots, active_slots, slot_decay_per_second, active, created_at, updated_at \
         FROM concurrency_limits WHERE name = ?1",
        params![name],
        limit_row_to_json,
    )
    .optional()
    .map_err(|e| e.to_string())
}

/// Create or replace a concurrency limit by name.
pub fn upsert_limit(conn: &Connection, body: &Value) -> Result<Value, String> {
    ensure_schema(conn)?;
    let name = body
        .get("name")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "missing name".to_string())?
        .trim();
    if name.is_empty() {
        return Err("name must be non-empty".to_string());
    }
    let limit = body
        .get("limit")
        .and_then(|v| v.as_i64().or_else(|| v.as_u64().map(|u| u as i64)))
        .ok_or_else(|| "missing limit".to_string())?;
    if limit < 0 {
        return Err("limit must be >= 0".to_string());
    }
    let decay = body.get("slot_decay_per_second").and_then(|v| v.as_f64());
    if let Some(d) = decay {
        if d <= 0.0 {
            return Err("slot_decay_per_second must be > 0".to_string());
        }
    }
    let active = body.get("active").and_then(|v| v.as_bool()).unwrap_or(true);
    let now = parse_now(body.get("now").and_then(|v| v.as_str()))?.to_rfc3339();

    if let Some(existing) = select_limit_by_name(conn, name)? {
        let id = existing
            .get("id")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "corrupt limit row".to_string())?;
        conn.execute(
            "UPDATE concurrency_limits SET limit_slots = ?1, slot_decay_per_second = ?2, \
             active = ?3, updated_at = ?4 WHERE id = ?5",
            params![limit, decay, if active { 1 } else { 0 }, now, id],
        )
        .map_err(|e| e.to_string())?;
        return select_limit_by_name(conn, name)?
            .ok_or_else(|| "limit missing after update".to_string());
    }

    let id = Uuid::new_v4().to_string();
    conn.execute(
        "INSERT INTO concurrency_limits(id, name, limit_slots, active_slots, slot_decay_per_second, active, created_at, updated_at) \
         VALUES(?1, ?2, ?3, 0, ?4, ?5, ?6, ?6)",
        params![id, name, limit, decay, if active { 1 } else { 0 }, now],
    )
    .map_err(|e| e.to_string())?;
    select_limit_by_name(conn, name)?.ok_or_else(|| "limit missing after insert".to_string())
}

pub fn delete_limit(conn: &Connection, name: &str) -> Result<Value, String> {
    ensure_schema(conn)?;
    let Some(lim) = select_limit_by_name(conn, name)? else {
        return Ok(json!({"ok": true, "deleted": false}));
    };
    let id = lim.get("id").and_then(|v| v.as_str()).unwrap();
    conn.execute(
        "DELETE FROM concurrency_leases WHERE limit_id = ?1",
        params![id],
    )
    .map_err(|e| e.to_string())?;
    conn.execute("DELETE FROM concurrency_limits WHERE id = ?1", params![id])
        .map_err(|e| e.to_string())?;
    Ok(json!({"ok": true, "deleted": true}))
}

pub fn get_limit(conn: &Connection, name: &str) -> Result<Value, String> {
    ensure_schema(conn)?;
    match select_limit_by_name(conn, name)? {
        Some(v) => Ok(json!({"ok": true, "limit": v})),
        None => Ok(json!({"ok": true, "limit": null})),
    }
}

pub fn list_limits(conn: &Connection) -> Result<Value, String> {
    ensure_schema(conn)?;
    let mut stmt = conn
        .prepare(
            "SELECT id, name, limit_slots, active_slots, slot_decay_per_second, active, created_at, updated_at \
             FROM concurrency_limits ORDER BY name ASC",
        )
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([], limit_row_to_json)
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    Ok(json!({"ok": true, "limits": rows}))
}

struct LimitState {
    id: String,
    name: String,
    limit_slots: i64,
    active_slots: i64,
    slot_decay_per_second: Option<f64>,
    active: bool,
}

fn load_limit_tx(tx: &Transaction<'_>, name: &str) -> Result<Option<LimitState>, String> {
    tx.query_row(
        "SELECT id, name, limit_slots, active_slots, slot_decay_per_second, active \
         FROM concurrency_limits WHERE name = ?1",
        params![name],
        |row| {
            Ok(LimitState {
                id: row.get(0)?,
                name: row.get(1)?,
                limit_slots: row.get(2)?,
                active_slots: row.get(3)?,
                slot_decay_per_second: row.get(4)?,
                active: row.get::<_, i64>(5)? != 0,
            })
        },
    )
    .optional()
    .map_err(|e| e.to_string())
}

/// Reclaim expired leases and refresh `active_slots` for affected limits.
pub fn reclaim_expired(conn: &Connection, now_iso: Option<&str>) -> Result<u64, String> {
    ensure_schema(conn)?;
    let now = parse_now(now_iso)?.to_rfc3339();
    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    let n = reclaim_expired_tx(&tx, &now)?;
    tx.commit().map_err(|e| e.to_string())?;
    Ok(n)
}

fn reclaim_expired_tx(tx: &Transaction<'_>, now: &str) -> Result<u64, String> {
    let mut stmt = tx
        .prepare("SELECT id, limit_id, occupy FROM concurrency_leases WHERE expires_at <= ?1")
        .map_err(|e| e.to_string())?;
    let expired: Vec<(String, String, i64)> = stmt
        .query_map(params![now], |row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?))
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    drop(stmt);

    let mut by_limit: HashMap<String, i64> = HashMap::new();
    for (lease_id, limit_id, occupy) in &expired {
        tx.execute(
            "DELETE FROM concurrency_leases WHERE id = ?1",
            params![lease_id],
        )
        .map_err(|e| e.to_string())?;
        *by_limit.entry(limit_id.clone()).or_insert(0) += occupy;
    }
    for (limit_id, freed) in by_limit {
        tx.execute(
            "UPDATE concurrency_limits SET active_slots = CASE \
                WHEN active_slots > ?1 THEN active_slots - ?1 ELSE 0 END, \
                updated_at = ?2 WHERE id = ?3",
            params![freed, now, limit_id],
        )
        .map_err(|e| e.to_string())?;
    }
    Ok(expired.len() as u64)
}

/// Acquire slots from one or more limit names atomically.
///
/// Request body:
/// - `names`: string or array of strings
/// - `occupy`: positive int (default 1)
/// - `mode`: `concurrency` | `rate_limit` (default concurrency)
/// - `lease_duration`: seconds (default 300) for concurrency mode
/// - `strict`: bool — error if any named limit is missing/inactive
/// - `holder_type` / `holder_id`: optional
/// - `now`: optional RFC3339 (tests)
#[allow(clippy::too_many_lines)] // split with concurrency_ops in the crate-layering pass
pub fn acquire(conn: &Connection, body: &Value) -> Result<Value, String> {
    ensure_schema(conn)?;
    let mut names: Vec<String> = match body.get("names") {
        Some(Value::String(s)) => vec![s.clone()],
        Some(Value::Array(arr)) => arr
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .collect(),
        _ => return Err("names must be a string or array".to_string()),
    };
    if names.is_empty() {
        return Err("names must be non-empty".to_string());
    }
    names.sort();
    names.dedup();

    let occupy = body
        .get("occupy")
        .and_then(|v| v.as_i64().or_else(|| v.as_u64().map(|u| u as i64)))
        .unwrap_or(1);
    if occupy <= 0 {
        return Err("occupy must be > 0".to_string());
    }
    let mode = body
        .get("mode")
        .and_then(|v| v.as_str())
        .unwrap_or("concurrency");
    if mode != "concurrency" && mode != "rate_limit" {
        return Err("mode must be concurrency or rate_limit".to_string());
    }
    let lease_duration = body
        .get("lease_duration")
        .and_then(|v| v.as_f64().or_else(|| v.as_i64().map(|i| i as f64)))
        .unwrap_or(300.0)
        .max(1.0);
    let strict = body
        .get("strict")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let holder_type = body.get("holder_type").and_then(|v| v.as_str());
    let holder_id = body.get("holder_id").and_then(|v| v.as_str());
    let now_dt = parse_now(body.get("now").and_then(|v| v.as_str()))?;
    let now = now_dt.to_rfc3339();

    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    reclaim_expired_tx(&tx, &now)?;

    // Resolve limits in sorted name order (deadlock-free).
    let mut resolved: Vec<(String, Option<LimitState>)> = Vec::with_capacity(names.len());
    for name in &names {
        resolved.push((name.clone(), load_limit_tx(&tx, name)?));
    }

    // Missing / inactive handling
    let mut effective: Vec<LimitState> = Vec::new();
    for (name, lim) in &resolved {
        match lim {
            None => {
                if strict {
                    return Ok(json!({
                        "ok": false,
                        "status": "missing",
                        "error": {"code": "missing_limit", "name": name},
                    }));
                }
            }
            Some(l) if !l.active => {
                if strict {
                    return Ok(json!({
                        "ok": false,
                        "status": "inactive",
                        "error": {"code": "inactive_limit", "name": name},
                    }));
                }
            }
            Some(l) => effective.push(LimitState {
                id: l.id.clone(),
                name: l.name.clone(),
                limit_slots: l.limit_slots,
                active_slots: l.active_slots,
                slot_decay_per_second: l.slot_decay_per_second,
                active: l.active,
            }),
        }
    }

    if effective.is_empty() {
        return Ok(json!({
            "ok": true,
            "status": "bypassed",
            "lease_ids": [],
            "leases": [],
        }));
    }

    if mode == "rate_limit" {
        for l in &effective {
            if l.slot_decay_per_second.is_none() {
                return Ok(json!({
                    "ok": false,
                    "status": "decay_required",
                    "error": {
                        "code": "decay_required",
                        "name": l.name,
                        "message": "rate_limit requires slot_decay_per_second on all limits",
                    },
                }));
            }
        }
    }

    // Capacity / deny checks
    for l in &effective {
        if l.limit_slots == 0 {
            return Ok(json!({
                "ok": true,
                "status": "denied",
                "name": l.name,
                "lease_ids": [],
                "leases": [],
            }));
        }
        if l.active_slots + occupy > l.limit_slots {
            return Ok(json!({
                "ok": true,
                "status": "would_block",
                "name": l.name,
                "lease_ids": [],
                "leases": [],
            }));
        }
    }

    let mut leases = Vec::new();
    let mut lease_ids = Vec::new();
    for l in &effective {
        let lease_id = Uuid::new_v4().to_string();
        let expires_at = if mode == "rate_limit" {
            let decay = l.slot_decay_per_second.unwrap();
            let secs = (occupy as f64) / decay;
            (now_dt + Duration::milliseconds((secs * 1000.0).ceil() as i64)).to_rfc3339()
        } else {
            (now_dt + Duration::milliseconds((lease_duration * 1000.0) as i64)).to_rfc3339()
        };
        tx.execute(
            "INSERT INTO concurrency_leases(id, limit_id, occupy, holder_type, holder_id, acquired_at, expires_at, mode) \
             VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                lease_id,
                l.id,
                occupy,
                holder_type,
                holder_id,
                now,
                expires_at,
                mode
            ],
        )
        .map_err(|e| e.to_string())?;
        tx.execute(
            "UPDATE concurrency_limits SET active_slots = active_slots + ?1, updated_at = ?2 WHERE id = ?3",
            params![occupy, now, l.id],
        )
        .map_err(|e| e.to_string())?;
        lease_ids.push(json!(lease_id));
        leases.push(json!({
            "lease_id": lease_id,
            "limit_id": l.id,
            "name": l.name,
            "occupy": occupy,
            "expires_at": expires_at,
            "mode": mode,
        }));
    }

    tx.commit().map_err(|e| e.to_string())?;
    Ok(json!({
        "ok": true,
        "status": "acquired",
        "lease_ids": lease_ids,
        "leases": leases,
    }))
}

/// Release leases by id. Idempotent for unknown ids.
pub fn release(conn: &Connection, body: &Value) -> Result<Value, String> {
    ensure_schema(conn)?;
    let lease_ids: Vec<String> = match body.get("lease_ids") {
        Some(Value::String(s)) => vec![s.clone()],
        Some(Value::Array(arr)) => arr
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .collect(),
        _ => return Err("lease_ids must be a string or array".to_string()),
    };
    let now = parse_now(body.get("now").and_then(|v| v.as_str()))?.to_rfc3339();
    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    let mut released = 0u64;
    for lease_id in &lease_ids {
        let row: Option<(String, i64)> = tx
            .query_row(
                "SELECT limit_id, occupy FROM concurrency_leases WHERE id = ?1",
                params![lease_id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()
            .map_err(|e| e.to_string())?;
        let Some((limit_id, occupy)) = row else {
            continue;
        };
        tx.execute(
            "DELETE FROM concurrency_leases WHERE id = ?1",
            params![lease_id],
        )
        .map_err(|e| e.to_string())?;
        tx.execute(
            "UPDATE concurrency_limits SET active_slots = CASE \
                WHEN active_slots > ?1 THEN active_slots - ?1 ELSE 0 END, \
                updated_at = ?2 WHERE id = ?3",
            params![occupy, now, limit_id],
        )
        .map_err(|e| e.to_string())?;
        released += 1;
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(json!({"ok": true, "released": released}))
}

/// Release all leases whose ``holder_id`` is in the request list. Idempotent.
pub fn release_by_holders(conn: &Connection, body: &Value) -> Result<Value, String> {
    ensure_schema(conn)?;
    let holder_ids: Vec<String> = match body.get("holder_ids") {
        Some(Value::String(s)) => {
            let trimmed = s.trim();
            if trimmed.is_empty() {
                vec![]
            } else {
                vec![trimmed.to_string()]
            }
        }
        Some(Value::Array(arr)) => arr
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .filter(|s| !s.is_empty())
            .collect(),
        _ => return Err("holder_ids must be a string or array".to_string()),
    };
    if holder_ids.is_empty() {
        return Ok(json!({"ok": true, "released": 0}));
    }
    let now = parse_now(body.get("now").and_then(|v| v.as_str()))?.to_rfc3339();
    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    let mut released = 0u64;
    for holder_id in &holder_ids {
        let rows: Vec<(String, String, i64)> = {
            let mut stmt = tx
                .prepare("SELECT id, limit_id, occupy FROM concurrency_leases WHERE holder_id = ?1")
                .map_err(|e| e.to_string())?;
            let mapped = stmt
                .query_map(params![holder_id], |r| {
                    Ok((r.get(0)?, r.get(1)?, r.get(2)?))
                })
                .map_err(|e| e.to_string())?;
            mapped
                .collect::<Result<Vec<_>, _>>()
                .map_err(|e| e.to_string())?
        };
        for (lease_id, limit_id, occupy) in rows {
            tx.execute(
                "DELETE FROM concurrency_leases WHERE id = ?1",
                params![lease_id],
            )
            .map_err(|e| e.to_string())?;
            tx.execute(
                "UPDATE concurrency_limits SET active_slots = CASE \
                    WHEN active_slots > ?1 THEN active_slots - ?1 ELSE 0 END, \
                    updated_at = ?2 WHERE id = ?3",
                params![occupy, now, limit_id],
            )
            .map_err(|e| e.to_string())?;
            released += 1;
        }
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(json!({"ok": true, "released": released}))
}

/// Extend lease expiry for concurrency-mode holds.
pub fn renew(conn: &Connection, body: &Value) -> Result<Value, String> {
    ensure_schema(conn)?;
    let lease_ids: Vec<String> = match body.get("lease_ids") {
        Some(Value::String(s)) => vec![s.clone()],
        Some(Value::Array(arr)) => arr
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .collect(),
        _ => return Err("lease_ids must be a string or array".to_string()),
    };
    let lease_duration = body
        .get("lease_duration")
        .and_then(|v| v.as_f64().or_else(|| v.as_i64().map(|i| i as f64)))
        .unwrap_or(300.0)
        .max(1.0);
    let now_dt = parse_now(body.get("now").and_then(|v| v.as_str()))?;
    let expires_at =
        (now_dt + Duration::milliseconds((lease_duration * 1000.0) as i64)).to_rfc3339();
    let mut renewed = 0u64;
    for lease_id in &lease_ids {
        let n = conn
            .execute(
                "UPDATE concurrency_leases SET expires_at = ?1 WHERE id = ?2",
                params![expires_at, lease_id],
            )
            .map_err(|e| e.to_string())?;
        renewed += n as u64;
    }
    Ok(json!({"ok": true, "renewed": renewed, "expires_at": expires_at}))
}

pub fn tag_limit_name(tag: &str) -> String {
    format!("tag:{tag}")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn open_db() -> Connection {
        let conn = Connection::open_in_memory().expect("db");
        ensure_schema(&conn).expect("schema");
        conn
    }

    #[test]
    fn upsert_get_list_delete() {
        let conn = open_db();
        let lim = upsert_limit(
            &conn,
            &json!({"name": "database", "limit": 2, "now": "2020-01-01T00:00:00Z"}),
        )
        .unwrap();
        assert_eq!(lim["name"], "database");
        assert_eq!(lim["limit"], 2);

        let listed = list_limits(&conn).unwrap();
        assert_eq!(listed["limits"].as_array().unwrap().len(), 1);

        delete_limit(&conn, "database").unwrap();
        let gone = get_limit(&conn, "database").unwrap();
        assert!(gone["limit"].is_null());
    }

    #[test]
    fn acquire_respects_limit_and_releases() {
        let conn = open_db();
        upsert_limit(
            &conn,
            &json!({"name": "db", "limit": 2, "now": "2020-01-01T00:00:00Z"}),
        )
        .unwrap();

        let a1 = acquire(
            &conn,
            &json!({
                "names": ["db"], "occupy": 1, "now": "2020-01-01T00:00:00Z",
                "lease_duration": 60
            }),
        )
        .unwrap();
        assert_eq!(a1["status"], "acquired");
        let a2 = acquire(
            &conn,
            &json!({
                "names": ["db"], "occupy": 1, "now": "2020-01-01T00:00:00Z",
                "lease_duration": 60
            }),
        )
        .unwrap();
        assert_eq!(a2["status"], "acquired");
        let blocked = acquire(
            &conn,
            &json!({
                "names": ["db"], "occupy": 1, "now": "2020-01-01T00:00:00Z",
                "lease_duration": 60
            }),
        )
        .unwrap();
        assert_eq!(blocked["status"], "would_block");

        let lids = a1["lease_ids"].clone();
        release(
            &conn,
            &json!({"lease_ids": lids, "now": "2020-01-01T00:00:01Z"}),
        )
        .unwrap();
        let a3 = acquire(
            &conn,
            &json!({
                "names": ["db"], "occupy": 1, "now": "2020-01-01T00:00:01Z",
                "lease_duration": 60
            }),
        )
        .unwrap();
        assert_eq!(a3["status"], "acquired");
    }

    #[test]
    fn multi_name_acquire_is_all_or_nothing() {
        let conn = open_db();
        upsert_limit(
            &conn,
            &json!({"name": "a", "limit": 1, "now": "2020-01-01T00:00:00Z"}),
        )
        .unwrap();
        upsert_limit(
            &conn,
            &json!({"name": "b", "limit": 1, "now": "2020-01-01T00:00:00Z"}),
        )
        .unwrap();
        // Fill b
        acquire(
            &conn,
            &json!({"names": ["b"], "now": "2020-01-01T00:00:00Z", "lease_duration": 60}),
        )
        .unwrap();
        let out = acquire(
            &conn,
            &json!({
                "names": ["a", "b"],
                "now": "2020-01-01T00:00:00Z",
                "lease_duration": 60
            }),
        )
        .unwrap();
        assert_eq!(out["status"], "would_block");
        // a must still be free
        let a = acquire(
            &conn,
            &json!({"names": ["a"], "now": "2020-01-01T00:00:00Z", "lease_duration": 60}),
        )
        .unwrap();
        assert_eq!(a["status"], "acquired");
    }

    #[test]
    fn missing_limit_bypasses_unless_strict() {
        let conn = open_db();
        let soft = acquire(
            &conn,
            &json!({"names": ["missing"], "now": "2020-01-01T00:00:00Z"}),
        )
        .unwrap();
        assert_eq!(soft["status"], "bypassed");
        let hard = acquire(
            &conn,
            &json!({"names": ["missing"], "strict": true, "now": "2020-01-01T00:00:00Z"}),
        )
        .unwrap();
        assert_eq!(hard["ok"], false);
        assert_eq!(hard["status"], "missing");
    }

    #[test]
    fn limit_zero_denies() {
        let conn = open_db();
        upsert_limit(
            &conn,
            &json!({"name": "tag:db", "limit": 0, "now": "2020-01-01T00:00:00Z"}),
        )
        .unwrap();
        let out = acquire(
            &conn,
            &json!({"names": ["tag:db"], "now": "2020-01-01T00:00:00Z"}),
        )
        .unwrap();
        assert_eq!(out["status"], "denied");
    }

    #[test]
    fn reclaim_expired_frees_slots() {
        let conn = open_db();
        upsert_limit(
            &conn,
            &json!({"name": "db", "limit": 1, "now": "2020-01-01T00:00:00Z"}),
        )
        .unwrap();
        acquire(
            &conn,
            &json!({
                "names": ["db"],
                "now": "2020-01-01T00:00:00Z",
                "lease_duration": 1
            }),
        )
        .unwrap();
        let blocked = acquire(
            &conn,
            &json!({
                "names": ["db"],
                "now": "2020-01-01T00:00:00.500Z",
                "lease_duration": 1
            }),
        )
        .unwrap();
        assert_eq!(blocked["status"], "would_block");
        let free = acquire(
            &conn,
            &json!({
                "names": ["db"],
                "now": "2020-01-01T00:00:02Z",
                "lease_duration": 1
            }),
        )
        .unwrap();
        assert_eq!(free["status"], "acquired");
    }

    #[test]
    fn rate_limit_requires_decay_and_expires() {
        let conn = open_db();
        upsert_limit(
            &conn,
            &json!({"name": "api", "limit": 1, "now": "2020-01-01T00:00:00Z"}),
        )
        .unwrap();
        let no_decay = acquire(
            &conn,
            &json!({
                "names": ["api"],
                "mode": "rate_limit",
                "now": "2020-01-01T00:00:00Z"
            }),
        )
        .unwrap();
        assert_eq!(no_decay["status"], "decay_required");

        upsert_limit(
            &conn,
            &json!({
                "name": "api",
                "limit": 1,
                "slot_decay_per_second": 1.0,
                "now": "2020-01-01T00:00:00Z"
            }),
        )
        .unwrap();
        let a1 = acquire(
            &conn,
            &json!({
                "names": ["api"],
                "mode": "rate_limit",
                "occupy": 1,
                "now": "2020-01-01T00:00:00Z"
            }),
        )
        .unwrap();
        assert_eq!(a1["status"], "acquired");
        let blocked = acquire(
            &conn,
            &json!({
                "names": ["api"],
                "mode": "rate_limit",
                "now": "2020-01-01T00:00:00.100Z"
            }),
        )
        .unwrap();
        assert_eq!(blocked["status"], "would_block");
        let later = acquire(
            &conn,
            &json!({
                "names": ["api"],
                "mode": "rate_limit",
                "now": "2020-01-01T00:00:01.100Z"
            }),
        )
        .unwrap();
        assert_eq!(later["status"], "acquired");
    }

    #[test]
    fn release_is_idempotent() {
        let conn = open_db();
        upsert_limit(
            &conn,
            &json!({"name": "db", "limit": 1, "now": "2020-01-01T00:00:00Z"}),
        )
        .unwrap();
        let a = acquire(
            &conn,
            &json!({"names": ["db"], "now": "2020-01-01T00:00:00Z", "lease_duration": 60}),
        )
        .unwrap();
        let lids = a["lease_ids"].clone();
        let r1 = release(
            &conn,
            &json!({"lease_ids": lids.clone(), "now": "2020-01-01T00:00:01Z"}),
        )
        .unwrap();
        assert_eq!(r1["released"], 1);
        let r2 = release(
            &conn,
            &json!({"lease_ids": lids, "now": "2020-01-01T00:00:02Z"}),
        )
        .unwrap();
        assert_eq!(r2["released"], 0);
    }

    #[test]
    fn release_by_holders_frees_slots() {
        let conn = open_db();
        upsert_limit(
            &conn,
            &json!({"name": "db", "limit": 1, "now": "2020-01-01T00:00:00Z"}),
        )
        .unwrap();
        let a = acquire(
            &conn,
            &json!({
                "names": ["db"],
                "now": "2020-01-01T00:00:00Z",
                "lease_duration": 60,
                "holder_type": "task_run",
                "holder_id": "task-1"
            }),
        )
        .unwrap();
        assert_eq!(a["status"], "acquired");
        let blocked = acquire(
            &conn,
            &json!({"names": ["db"], "now": "2020-01-01T00:00:00Z", "lease_duration": 60}),
        )
        .unwrap();
        assert_eq!(blocked["status"], "would_block");
        let out = release_by_holders(
            &conn,
            &json!({"holder_ids": ["task-1"], "now": "2020-01-01T00:00:01Z"}),
        )
        .unwrap();
        assert_eq!(out["released"], 1);
        let again = acquire(
            &conn,
            &json!({"names": ["db"], "now": "2020-01-01T00:00:02Z", "lease_duration": 60}),
        )
        .unwrap();
        assert_eq!(again["status"], "acquired");
        let empty = release_by_holders(&conn, &json!({"holder_ids": []})).unwrap();
        assert_eq!(empty["released"], 0);
    }

    #[test]
    fn tag_limit_name_helper() {
        assert_eq!(tag_limit_name("db"), "tag:db");
    }
}
