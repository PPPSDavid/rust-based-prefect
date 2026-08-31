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

pub(crate) fn parse_now(now_iso: Option<&str>) -> Result<DateTime<Utc>, String> {
    match now_iso {
        Some(s) => DateTime::parse_from_rfc3339(s)
            .map(|dt| dt.with_timezone(&Utc))
            .map_err(|e| format!("invalid now: {e}")),
        None => Ok(Utc::now()),
    }
}

pub(crate) fn ensure_schema(conn: &Connection) -> Result<(), String> {
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

pub(crate) struct LimitState {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) limit_slots: i64,
    pub(crate) active_slots: i64,
    pub(crate) slot_decay_per_second: Option<f64>,
    pub(crate) active: bool,
}

pub(crate) fn load_limit_tx(
    tx: &Transaction<'_>,
    name: &str,
) -> Result<Option<LimitState>, String> {
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

pub(crate) fn reclaim_expired_tx(tx: &Transaction<'_>, now: &str) -> Result<u64, String> {
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

mod acquire;
pub use acquire::acquire;

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
mod tests;
