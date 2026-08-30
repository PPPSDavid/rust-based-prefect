use chrono::{DateTime, Duration, Utc};
use rusqlite::{params, Connection, Transaction};
use serde_json::{json, Value};
use uuid::Uuid;

use super::{ensure_schema, load_limit_tx, parse_now, reclaim_expired_tx, LimitState};

struct AcquireSpec<'a> {
    names: Vec<String>,
    occupy: i64,
    mode: &'a str,
    lease_duration: f64,
    strict: bool,
    holder_type: Option<&'a str>,
    holder_id: Option<&'a str>,
    now_dt: DateTime<Utc>,
}

fn parse_names(body: &Value) -> Result<Vec<String>, String> {
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
    Ok(names)
}

fn parse_spec<'a>(body: &'a Value) -> Result<AcquireSpec<'a>, String> {
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
    Ok(AcquireSpec {
        names: parse_names(body)?,
        occupy,
        mode,
        lease_duration: body
            .get("lease_duration")
            .and_then(|v| v.as_f64().or_else(|| v.as_i64().map(|i| i as f64)))
            .unwrap_or(300.0)
            .max(1.0),
        strict: body
            .get("strict")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        holder_type: body.get("holder_type").and_then(|v| v.as_str()),
        holder_id: body.get("holder_id").and_then(|v| v.as_str()),
        now_dt: parse_now(body.get("now").and_then(|v| v.as_str()))?,
    })
}

fn collect_effective(
    resolved: Vec<(String, Option<LimitState>)>,
    strict: bool,
) -> Result<Vec<LimitState>, Value> {
    let mut effective: Vec<LimitState> = Vec::new();
    for (name, lim) in resolved {
        match lim {
            None if strict => {
                return Err(json!({
                    "ok": false,
                    "status": "missing",
                    "error": {"code": "missing_limit", "name": name},
                }));
            }
            None => {}
            Some(l) if !l.active && strict => {
                return Err(json!({
                    "ok": false,
                    "status": "inactive",
                    "error": {"code": "inactive_limit", "name": name},
                }));
            }
            Some(l) if l.active => effective.push(l),
            Some(_) => {}
        }
    }
    Ok(effective)
}

fn capacity_error(effective: &[LimitState], occupy: i64, mode: &str) -> Option<Value> {
    if mode == "rate_limit" {
        for l in effective {
            if l.slot_decay_per_second.is_none() {
                return Some(json!({
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
    for l in effective {
        if l.limit_slots == 0 {
            return Some(json!({
                "ok": true,
                "status": "denied",
                "name": l.name,
                "lease_ids": [],
                "leases": [],
            }));
        }
        if l.active_slots + occupy > l.limit_slots {
            return Some(json!({
                "ok": true,
                "status": "would_block",
                "name": l.name,
                "lease_ids": [],
                "leases": [],
            }));
        }
    }
    None
}

fn insert_leases(
    tx: &Transaction<'_>,
    spec: &AcquireSpec<'_>,
    effective: &[LimitState],
    now: &str,
) -> Result<(Vec<Value>, Vec<Value>), String> {
    let mut leases = Vec::new();
    let mut lease_ids = Vec::new();
    for l in effective {
        let lease_id = Uuid::new_v4().to_string();
        let expires_at = if spec.mode == "rate_limit" {
            let decay = l.slot_decay_per_second.unwrap();
            let secs = (spec.occupy as f64) / decay;
            (spec.now_dt + Duration::milliseconds((secs * 1000.0).ceil() as i64)).to_rfc3339()
        } else {
            (spec.now_dt + Duration::milliseconds((spec.lease_duration * 1000.0) as i64))
                .to_rfc3339()
        };
        tx.execute(
            "INSERT INTO concurrency_leases(id, limit_id, occupy, holder_type, holder_id, acquired_at, expires_at, mode) \
             VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                lease_id,
                l.id,
                spec.occupy,
                spec.holder_type,
                spec.holder_id,
                now,
                expires_at,
                spec.mode
            ],
        )
        .map_err(|e| e.to_string())?;
        tx.execute(
            "UPDATE concurrency_limits SET active_slots = active_slots + ?1, updated_at = ?2 WHERE id = ?3",
            params![spec.occupy, now, l.id],
        )
        .map_err(|e| e.to_string())?;
        lease_ids.push(json!(lease_id));
        leases.push(json!({
            "lease_id": lease_id,
            "limit_id": l.id,
            "name": l.name,
            "occupy": spec.occupy,
            "expires_at": expires_at,
            "mode": spec.mode,
        }));
    }
    Ok((lease_ids, leases))
}

/// Acquire slots from one or more limit names atomically.
pub fn acquire(conn: &Connection, body: &Value) -> Result<Value, String> {
    ensure_schema(conn)?;
    let spec = parse_spec(body)?;
    let now = spec.now_dt.to_rfc3339();
    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    reclaim_expired_tx(&tx, &now)?;
    let mut resolved = Vec::with_capacity(spec.names.len());
    for name in &spec.names {
        resolved.push((name.clone(), load_limit_tx(&tx, name)?));
    }
    let effective = match collect_effective(resolved, spec.strict) {
        Ok(v) => v,
        Err(early) => return Ok(early),
    };
    if effective.is_empty() {
        return Ok(json!({
            "ok": true,
            "status": "bypassed",
            "lease_ids": [],
            "leases": [],
        }));
    }
    if let Some(early) = capacity_error(&effective, spec.occupy, spec.mode) {
        return Ok(early);
    }
    let (lease_ids, leases) = insert_leases(&tx, &spec, &effective, &now)?;
    tx.commit().map_err(|e| e.to_string())?;
    Ok(json!({
        "ok": true,
        "status": "acquired",
        "lease_ids": lease_ids,
        "leases": leases,
    }))
}
