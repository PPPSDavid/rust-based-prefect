//! Deployment queue, scheduling, and worker heartbeats — SQLite hot paths kept in Rust.
//! Called from `ironflow_control` when `bind_db` has attached a connection.

use std::str::FromStr;

use chrono::{DateTime, Duration, Utc};
use cron::Schedule;
use rusqlite::{params, Connection, OptionalExtension, Result as SqlResult};
use serde_json::{json, Value};
use uuid::Uuid;

const DEFAULT_WORK_POOL_ID: &str = "default-process-pool";

fn now_iso() -> String {
    Utc::now().to_rfc3339()
}

fn merge_parameters(default_parameters: &str, requested: Option<&Value>) -> Result<String, String> {
    let mut base: Value = serde_json::from_str(default_parameters).unwrap_or(json!({}));
    if let Some(req) = requested {
        if let (Some(bo), Some(ro)) = (base.as_object_mut(), req.as_object()) {
            for (k, v) in ro {
                bo.insert(k.clone(), v.clone());
            }
        }
    }
    serde_json::to_string(&base).map_err(|e| e.to_string())
}

fn deployment_row_to_json(row: &rusqlite::Row) -> SqlResult<Value> {
    let default_parameters: Value =
        serde_json::from_str(row.get::<_, String>("default_parameters")?.as_str())
            .unwrap_or(json!({}));
    Ok(json!({
        "id": row.get::<_, String>("id")?,
        "name": row.get::<_, String>("name")?,
        "flow_name": row.get::<_, String>("flow_name")?,
        "entrypoint": row.get::<_, Option<String>>("entrypoint")?,
        "path": row.get::<_, Option<String>>("path")?,
        "default_parameters": default_parameters,
        "paused": row.get::<_, i64>("paused")? != 0,
        "concurrency_limit": row.get::<_, Option<i64>>("concurrency_limit")?,
        "collision_strategy": row.get::<_, Option<String>>("collision_strategy")?.unwrap_or_else(|| "ENQUEUE".to_string()),
        "schedule_interval_seconds": row.get::<_, Option<i64>>("schedule_interval_seconds")?,
        "schedule_cron": row.get::<_, Option<String>>("schedule_cron")?,
        "schedule_rrule": row.get::<_, Option<String>>("schedule_rrule")?,
        "schedule_next_run_at": row.get::<_, Option<String>>("schedule_next_run_at")?,
        "schedule_enabled": row.get::<_, i64>("schedule_enabled")? != 0,
        "work_pool_id": row
            .get::<_, Option<String>>("work_pool_id")?
            .unwrap_or_else(|| DEFAULT_WORK_POOL_ID.to_string()),
        "created_at": row.get::<_, String>("created_at")?,
        "updated_at": row.get::<_, String>("updated_at")?,
    }))
}

fn deployment_run_row_to_json(row: &rusqlite::Row) -> SqlResult<Value> {
    let requested: Value =
        serde_json::from_str(row.get::<_, String>("requested_parameters")?.as_str())
            .unwrap_or(json!({}));
    let resolved: Value =
        serde_json::from_str(row.get::<_, String>("resolved_parameters")?.as_str())
            .unwrap_or(json!({}));
    Ok(json!({
        "id": row.get::<_, String>("id")?,
        "deployment_id": row.get::<_, String>("deployment_id")?,
        "status": row.get::<_, String>("status")?,
        "requested_parameters": requested,
        "resolved_parameters": resolved,
        "idempotency_key": row.get::<_, Option<String>>("idempotency_key")?,
        "worker_name": row.get::<_, Option<String>>("worker_name")?,
        "lease_until": row.get::<_, Option<String>>("lease_until")?,
        "flow_run_id": row.get::<_, Option<String>>("flow_run_id")?,
        "error": row.get::<_, Option<String>>("error")?,
        "created_at": row.get::<_, String>("created_at")?,
        "updated_at": row.get::<_, String>("updated_at")?,
        "started_at": row.get::<_, Option<String>>("started_at")?,
        "finished_at": row.get::<_, Option<String>>("finished_at")?,
    }))
}

/// Reclaim CLAIMED rows whose lease expired back to SCHEDULED.
pub fn reclaim_expired_claims(conn: &Connection) -> Result<u64, String> {
    let now = now_iso();
    let n = conn
        .execute(
            "UPDATE deployment_runs SET status = 'SCHEDULED', worker_name = NULL, lease_until = NULL, updated_at = ?1 \
             WHERE status = 'CLAIMED' AND lease_until IS NOT NULL AND lease_until < ?1",
            params![now],
        )
        .map_err(|e| e.to_string())?;
    Ok(n as u64)
}

fn count_exec_runs(conn: &Connection, deployment_id: &str) -> Result<i64, String> {
    let n: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM deployment_runs \
             WHERE deployment_id = ?1 AND status IN ('CLAIMED','RUNNING')",
            params![deployment_id],
            |row| row.get(0),
        )
        .map_err(|e| e.to_string())?;
    Ok(n)
}

/// Upsert worker heartbeat (ONLINE).
pub fn worker_heartbeat(conn: &Connection, worker_name: &str, work_pool_id: Option<&str>) -> Result<(), String> {
    let now = now_iso();
    conn.execute(
        "INSERT INTO workers(name,last_heartbeat,status,updated_at,work_pool_id) VALUES(?1,?2,'ONLINE',?3,?4) \
         ON CONFLICT(name) DO UPDATE SET last_heartbeat = excluded.last_heartbeat, \
         status = excluded.status, updated_at = excluded.updated_at, \
         work_pool_id = COALESCE(excluded.work_pool_id, workers.work_pool_id)",
        params![worker_name, now, now, work_pool_id],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// Mark workers OFFLINE if last_heartbeat is older than `stale_after` seconds.
pub fn reap_stale_workers(conn: &Connection, stale_after_seconds: i64) -> Result<u64, String> {
    let now = Utc::now();
    let cutoff = (now - Duration::seconds(stale_after_seconds)).to_rfc3339();
    let ts = now.to_rfc3339();
    let n = conn
        .execute(
            "UPDATE workers SET status = 'OFFLINE', updated_at = ?1 \
             WHERE status = 'ONLINE' AND last_heartbeat < ?2",
            params![ts, cutoff],
        )
        .map_err(|e| e.to_string())?;
    Ok(n as u64)
}

/// Claim the next eligible SCHEDULED run (respecting per-deployment concurrency limits).
pub fn claim_next_deployment_run(
    conn: &Connection,
    worker_name: &str,
    lease_seconds: i64,
    work_pool_id: Option<&str>,
) -> Result<Option<Value>, String> {
    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    worker_heartbeat(&tx, worker_name, work_pool_id)?;
    reclaim_expired_claims(&tx)?;

    let now_dt = Utc::now();
    let now = now_dt.to_rfc3339();
    let lease_until = (now_dt + Duration::seconds(lease_seconds.max(1))).to_rfc3339();

    let pool_filter = work_pool_id.unwrap_or(DEFAULT_WORK_POOL_ID);
    let candidate_id: Option<String> = tx
        .query_row(
            "SELECT dr.id FROM deployment_runs dr \
             INNER JOIN deployments d ON d.id = dr.deployment_id \
             WHERE dr.status = 'SCHEDULED' \
             AND COALESCE(d.work_pool_id, ?2) = ?1 \
             AND ( \
               d.concurrency_limit IS NULL \
               OR ( \
                 SELECT COUNT(*) FROM deployment_runs x \
                 WHERE x.deployment_id = dr.deployment_id \
                 AND x.status IN ('CLAIMED','RUNNING') \
               ) < d.concurrency_limit \
             ) \
             ORDER BY dr.created_at ASC \
             LIMIT 1",
            params![pool_filter, DEFAULT_WORK_POOL_ID],
            |row| row.get(0),
        )
        .optional()
        .map_err(|e| e.to_string())?;

    let Some(cid) = candidate_id else {
        tx.commit().map_err(|e| e.to_string())?;
        return Ok(None);
    };

    let updated = tx
        .execute(
            "UPDATE deployment_runs SET status = 'CLAIMED', worker_name = ?1, lease_until = ?2, updated_at = ?3 \
             WHERE id = ?4 AND status = 'SCHEDULED'",
            params![worker_name, lease_until, now, cid],
        )
        .map_err(|e| e.to_string())?;
    if updated == 0 {
        tx.commit().map_err(|e| e.to_string())?;
        return Ok(None);
    }

    let row = tx
        .query_row(
            "SELECT id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,\
             worker_name,lease_until,flow_run_id,error,created_at,updated_at,started_at,finished_at \
             FROM deployment_runs WHERE id = ?1 AND status = 'CLAIMED'",
            params![cid],
            |row| deployment_run_row_to_json(row),
        )
        .map_err(|e| e.to_string())?;
    tx.commit().map_err(|e| e.to_string())?;
    Ok(Some(row))
}

/// Insert a deployment run row (SCHEDULED or CANCELLED for CANCEL_NEW at capacity).
/// Runs entirely inside `tx` (no nested transaction).
pub fn trigger_deployment_run_tx(
    tx: &Connection,
    deployment_id: &str,
    requested: Option<&Value>,
    idempotency_key: Option<&str>,
) -> Result<Value, String> {
    let dep = tx
        .query_row(
            "SELECT id, default_parameters, paused, concurrency_limit, collision_strategy \
             FROM deployments WHERE id = ?1 LIMIT 1",
            params![deployment_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, Option<i64>>(3)?,
                    row.get::<_, Option<String>>(4)?,
                ))
            },
        )
        .optional()
        .map_err(|e| e.to_string())?;

    let Some((dep_id, default_parameters, paused, concurrency_limit, collision_strategy)) = dep
    else {
        return Err("deployment not found".to_string());
    };

    if paused != 0 {
        return Err("deployment is paused".to_string());
    }

    if let Some(ikey) = idempotency_key {
        let existing: Option<String> = tx
            .query_row(
                "SELECT id FROM deployment_runs WHERE deployment_id = ?1 AND idempotency_key = ?2 LIMIT 1",
                params![deployment_id, ikey],
                |row| row.get(0),
            )
            .optional()
            .map_err(|e| e.to_string())?;
        if let Some(rid) = existing {
            let row = tx
                .query_row(
                    "SELECT id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,\
                     worker_name,lease_until,flow_run_id,error,created_at,updated_at,started_at,finished_at \
                     FROM deployment_runs WHERE id = ?1",
                    params![rid],
                    |row| deployment_run_row_to_json(row),
                )
                .map_err(|e| e.to_string())?;
            return Ok(row);
        }
    }

    let requested_json = requested.cloned().unwrap_or(json!({}));
    let requested_str = serde_json::to_string(&requested_json).map_err(|e| e.to_string())?;
    let resolved_str = merge_parameters(&default_parameters, Some(&requested_json))?;

    let strategy = collision_strategy.unwrap_or_else(|| "ENQUEUE".to_string());
    let mut status = "SCHEDULED";
    let mut error: Option<String> = None;
    if let Some(lim) = concurrency_limit {
        if lim > 0 && strategy == "CANCEL_NEW" {
            let exec = count_exec_runs(tx, deployment_id)?;
            if exec >= lim {
                status = "CANCELLED";
                error = Some("concurrency limit reached".to_string());
            }
        }
    }

    let run_id = Uuid::new_v4().to_string();
    let now = now_iso();
    tx.execute(
        "INSERT INTO deployment_runs \
         (id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,\
          worker_name,lease_until,flow_run_id,error,created_at,updated_at,started_at,finished_at) \
         VALUES (?1,?2,?3,?4,?5,?6,NULL,NULL,NULL,?7,?8,?9,NULL,NULL)",
        params![
            run_id,
            dep_id,
            status,
            requested_str,
            resolved_str,
            idempotency_key,
            error,
            now,
            now,
        ],
    )
    .map_err(|e| e.to_string())?;

    tx.query_row(
        "SELECT id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,\
         worker_name,lease_until,flow_run_id,error,created_at,updated_at,started_at,finished_at \
         FROM deployment_runs WHERE id = ?1",
        params![run_id],
        |row| deployment_run_row_to_json(row),
    )
    .map_err(|e| e.to_string())
}

pub fn trigger_deployment_run(
    conn: &Connection,
    deployment_id: &str,
    requested: Option<&Value>,
    idempotency_key: Option<&str>,
) -> Result<Value, String> {
    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    let v = trigger_deployment_run_tx(&tx, deployment_id, requested, idempotency_key)?;
    tx.commit().map_err(|e| e.to_string())?;
    Ok(v)
}

/// Insert a deployment row or return existing by unique `name` (Python shim parity).
pub fn create_deployment(conn: &Connection, body: &Value) -> Result<Value, String> {
    let name = body
        .get("name")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "missing string field name".to_string())?;
    let flow_name = body
        .get("flow_name")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "missing string field flow_name".to_string())?;
    let existing: Option<Value> = conn
        .query_row(
            "SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,\
             concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,\
             schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at \
             FROM deployments WHERE name = ?1 LIMIT 1",
            params![name],
            |row| deployment_row_to_json(row),
        )
        .optional()
        .map_err(|e| e.to_string())?;
    if let Some(row) = existing {
        return Ok(row);
    }

    let work_pool_id = body
        .get("work_pool_id")
        .and_then(|v| v.as_str())
        .unwrap_or(DEFAULT_WORK_POOL_ID);

    let entrypoint = body.get("entrypoint").and_then(|v| v.as_str());
    let path = body.get("path").and_then(|v| v.as_str());
    let default_parameters =
        serde_json::to_string(body.get("default_parameters").unwrap_or(&json!({})))
            .map_err(|e| e.to_string())?;
    let paused = body
        .get("paused")
        .and_then(|v| v.as_bool())
        .unwrap_or(false) as i64;
    let concurrency_limit = body
        .get("concurrency_limit")
        .and_then(|v| v.as_i64())
        .or_else(|| {
            body.get("concurrency_limit")
                .and_then(|v| v.as_u64().map(|u| u as i64))
        });
    let collision_strategy = body
        .get("collision_strategy")
        .and_then(|v| v.as_str())
        .unwrap_or("ENQUEUE");
    let mut schedule_interval_seconds = body
        .get("schedule_interval_seconds")
        .and_then(|v| v.as_i64());
    let mut schedule_cron = body
        .get("schedule_cron")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let mut schedule_rrule = body
        .get("schedule_rrule")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let schedule_enabled = body
        .get("schedule_enabled")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let mut schedule_next_run_at = body
        .get("schedule_next_run_at")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

    if schedule_rrule
        .as_ref()
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false)
    {
        schedule_interval_seconds = None;
        schedule_cron = None;
    } else if schedule_cron
        .as_ref()
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false)
    {
        schedule_rrule = None;
        schedule_interval_seconds = None;
    } else if schedule_interval_seconds.map(|s| s > 0).unwrap_or(false) {
        schedule_cron = None;
        schedule_rrule = None;
    }

    if schedule_enabled {
        if schedule_interval_seconds.map(|s| s > 0).unwrap_or(false)
            && schedule_next_run_at.is_none()
        {
            schedule_next_run_at = Some(now_iso());
        }
        if schedule_cron
            .as_ref()
            .map(|s| !s.trim().is_empty())
            .unwrap_or(false)
            && schedule_next_run_at.is_none()
        {
            let expr = schedule_cron.as_ref().map(|s| s.as_str()).unwrap_or("");
            let next = next_cron_occurrence(expr, Utc::now())?;
            schedule_next_run_at = Some(next.to_rfc3339());
        }
        if schedule_rrule
            .as_ref()
            .map(|s| !s.trim().is_empty())
            .unwrap_or(false)
            && schedule_next_run_at.is_none()
        {
            let expr = schedule_rrule.as_ref().map(|s| s.as_str()).unwrap_or("");
            let next = next_rrule_occurrence(expr, Utc::now())?;
            schedule_next_run_at = Some(next.to_rfc3339());
        }
    }

    let deployment_id = Uuid::new_v4().to_string();
    let now = now_iso();
    conn.execute(
        "INSERT INTO deployments \
         (id,name,flow_name,entrypoint,path,default_parameters,paused,\
          concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,\
          schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at) \
         VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17)",
        params![
            deployment_id,
            name,
            flow_name,
            entrypoint,
            path,
            default_parameters,
            paused,
            concurrency_limit,
            collision_strategy,
            schedule_interval_seconds,
            schedule_cron,
            schedule_rrule,
            schedule_next_run_at,
            if schedule_enabled { 1 } else { 0 },
            work_pool_id,
            now,
            now,
        ],
    )
    .map_err(|e| e.to_string())?;

    conn.query_row(
        "SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,\
         concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,\
         schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at \
         FROM deployments WHERE id = ?1",
        params![deployment_id],
        |row| deployment_row_to_json(row),
    )
    .map_err(|e| e.to_string())
}

fn next_cron_occurrence(expr: &str, after: DateTime<Utc>) -> Result<DateTime<Utc>, String> {
    let schedule = Schedule::from_str(expr.trim()).map_err(|e| e.to_string())?;
    schedule
        .after(&after)
        .next()
        .ok_or_else(|| "cron expression has no upcoming occurrence".to_string())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RRuleFrequency {
    Minutely,
    Hourly,
    Daily,
    Weekly,
}

#[derive(Debug, Clone)]
struct SimpleRRule {
    freq: RRuleFrequency,
    interval: i64,
    until: Option<DateTime<Utc>>,
}

fn parse_rrule_datetime(value: &str) -> Result<DateTime<Utc>, String> {
    if let Ok(dt) = DateTime::parse_from_rfc3339(value) {
        return Ok(dt.with_timezone(&Utc));
    }
    let normalized = value.trim().trim_end_matches('Z');
    chrono::NaiveDateTime::parse_from_str(normalized, "%Y%m%dT%H%M%S")
        .map(|dt| dt.and_utc())
        .map_err(|e| format!("invalid RRule UNTIL: {e}"))
}

fn parse_simple_rrule(expr: &str) -> Result<SimpleRRule, String> {
    let mut freq: Option<RRuleFrequency> = None;
    let mut interval: i64 = 1;
    let mut until: Option<DateTime<Utc>> = None;
    for part in expr.split(';') {
        let trimmed = part.trim();
        if trimmed.is_empty() {
            continue;
        }
        let Some((raw_key, raw_value)) = trimmed.split_once('=') else {
            return Err(format!("invalid RRule component: {trimmed}"));
        };
        let key = raw_key.trim().to_ascii_uppercase();
        let value = raw_value.trim();
        match key.as_str() {
            "FREQ" => {
                freq = Some(match value.to_ascii_uppercase().as_str() {
                    "MINUTELY" => RRuleFrequency::Minutely,
                    "HOURLY" => RRuleFrequency::Hourly,
                    "DAILY" => RRuleFrequency::Daily,
                    "WEEKLY" => RRuleFrequency::Weekly,
                    other => return Err(format!("unsupported RRule FREQ: {other}")),
                });
            }
            "INTERVAL" => {
                interval = value
                    .parse::<i64>()
                    .map_err(|e| format!("invalid RRule INTERVAL: {e}"))?;
                if interval <= 0 {
                    return Err("RRule INTERVAL must be positive".to_string());
                }
            }
            "UNTIL" => {
                until = Some(parse_rrule_datetime(value)?);
            }
            "COUNT" => {
                return Err(
                    "RRule COUNT is not supported; use UNTIL or trigger fixed runs manually"
                        .to_string(),
                );
            }
            _ => {
                return Err(format!("unsupported RRule component: {key}"));
            }
        }
    }
    Ok(SimpleRRule {
        freq: freq.ok_or_else(|| "RRule FREQ is required".to_string())?,
        interval,
        until,
    })
}

fn next_rrule_occurrence(expr: &str, after: DateTime<Utc>) -> Result<DateTime<Utc>, String> {
    let rule = parse_simple_rrule(expr)?;
    let step = match rule.freq {
        RRuleFrequency::Minutely => Duration::minutes(rule.interval),
        RRuleFrequency::Hourly => Duration::hours(rule.interval),
        RRuleFrequency::Daily => Duration::days(rule.interval),
        RRuleFrequency::Weekly => Duration::weeks(rule.interval),
    };
    let next = after + step;
    if let Some(until) = rule.until {
        if next > until {
            return Err("RRule has no upcoming occurrence before UNTIL".to_string());
        }
    }
    Ok(next)
}

/// Partial update of a deployment row (`null` JSON fields mean leave unchanged).
pub fn update_deployment(conn: &Connection, body: &Value) -> Result<Value, String> {
    let deployment_id = body
        .get("deployment_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "missing string field deployment_id".to_string())?;

    let row = conn
        .query_row(
            "SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,\
             concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,\
             schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at \
             FROM deployments WHERE id = ?1 LIMIT 1",
            params![deployment_id],
            |row| deployment_row_to_json(row),
        )
        .optional()
        .map_err(|e| e.to_string())?;
    let Some(current) = row else {
        return Err("deployment not found".to_string());
    };

    let mut entrypoint = current.get("entrypoint").and_then(|v| {
        if v.is_null() {
            None
        } else {
            v.as_str().map(|s| s.to_string())
        }
    });
    let mut path = current.get("path").and_then(|v| {
        if v.is_null() {
            None
        } else {
            v.as_str().map(|s| s.to_string())
        }
    });
    let mut default_parameters = current
        .get("default_parameters")
        .cloned()
        .unwrap_or(json!({}));
    let mut paused = current
        .get("paused")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let mut concurrency_limit = current
        .get("concurrency_limit")
        .and_then(|v| v.as_i64().or_else(|| v.as_u64().map(|u| u as i64)));
    let mut collision_strategy = current
        .get("collision_strategy")
        .and_then(|v| v.as_str())
        .unwrap_or("ENQUEUE")
        .to_string();
    let mut schedule_interval_seconds = current
        .get("schedule_interval_seconds")
        .and_then(|v| v.as_i64());
    let mut schedule_cron = current
        .get("schedule_cron")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let mut schedule_rrule = current
        .get("schedule_rrule")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let mut schedule_next_run_at = current
        .get("schedule_next_run_at")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let mut schedule_enabled = current
        .get("schedule_enabled")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    if let Some(v) = body.get("entrypoint") {
        if v.is_null() {
            entrypoint = None;
        } else {
            entrypoint = v.as_str().map(|s| s.to_string());
        }
    }
    if let Some(v) = body.get("path") {
        if v.is_null() {
            path = None;
        } else {
            path = v.as_str().map(|s| s.to_string());
        }
    }
    if let Some(v) = body.get("default_parameters") {
        if !v.is_null() {
            default_parameters = v.clone();
        }
    }
    if let Some(v) = body.get("paused") {
        if !v.is_null() {
            paused = v.as_bool().unwrap_or(false);
        }
    }
    if let Some(v) = body.get("concurrency_limit") {
        if v.is_null() {
            concurrency_limit = None;
        } else {
            concurrency_limit = v.as_i64().or_else(|| v.as_u64().map(|u| u as i64));
        }
    }
    if let Some(v) = body.get("collision_strategy") {
        if !v.is_null() {
            collision_strategy = v.as_str().unwrap_or("ENQUEUE").to_string();
        }
    }
    if let Some(v) = body.get("schedule_interval_seconds") {
        if v.is_null() {
            schedule_interval_seconds = None;
        } else {
            schedule_interval_seconds = v.as_i64();
        }
    }
    if let Some(v) = body.get("schedule_cron") {
        if v.is_null() {
            schedule_cron = None;
        } else {
            schedule_cron = v.as_str().map(|s| s.to_string());
        }
    }
    if let Some(v) = body.get("schedule_rrule") {
        if v.is_null() {
            schedule_rrule = None;
        } else {
            schedule_rrule = v.as_str().map(|s| s.to_string());
        }
    }
    if let Some(v) = body.get("schedule_next_run_at") {
        if v.is_null() {
            schedule_next_run_at = None;
        } else {
            schedule_next_run_at = v.as_str().map(|s| s.to_string());
        }
    }
    if let Some(v) = body.get("schedule_enabled") {
        if !v.is_null() {
            schedule_enabled = v.as_bool().unwrap_or(false);
        }
    }

    if schedule_rrule
        .as_ref()
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false)
    {
        schedule_interval_seconds = None;
        schedule_cron = None;
    } else if schedule_cron
        .as_ref()
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false)
    {
        schedule_rrule = None;
        schedule_interval_seconds = None;
    } else if schedule_interval_seconds.map(|s| s > 0).unwrap_or(false) {
        schedule_cron = None;
        schedule_rrule = None;
    }

    if schedule_enabled {
        if schedule_interval_seconds.map(|s| s > 0).unwrap_or(false)
            && schedule_next_run_at.is_none()
        {
            schedule_next_run_at = Some(now_iso());
        }
        if schedule_cron
            .as_ref()
            .map(|s| !s.trim().is_empty())
            .unwrap_or(false)
            && schedule_next_run_at.is_none()
        {
            let expr = schedule_cron.as_ref().map(|s| s.as_str()).unwrap_or("");
            let next = next_cron_occurrence(expr, Utc::now())?;
            schedule_next_run_at = Some(next.to_rfc3339());
        }
        if schedule_rrule
            .as_ref()
            .map(|s| !s.trim().is_empty())
            .unwrap_or(false)
            && schedule_next_run_at.is_none()
        {
            let expr = schedule_rrule.as_ref().map(|s| s.as_str()).unwrap_or("");
            let next = next_rrule_occurrence(expr, Utc::now())?;
            schedule_next_run_at = Some(next.to_rfc3339());
        }
    }

    let default_parameters_str =
        serde_json::to_string(&default_parameters).map_err(|e| e.to_string())?;
    let ts = now_iso();
    conn.execute(
        "UPDATE deployments SET \
         entrypoint = ?1, path = ?2, default_parameters = ?3, paused = ?4, \
         concurrency_limit = ?5, collision_strategy = ?6, \
         schedule_interval_seconds = ?7, schedule_cron = ?8, schedule_rrule = ?9, schedule_next_run_at = ?10, \
         schedule_enabled = ?11, updated_at = ?12 \
         WHERE id = ?13",
        params![
            entrypoint,
            path,
            default_parameters_str,
            if paused { 1 } else { 0 },
            concurrency_limit,
            collision_strategy,
            schedule_interval_seconds,
            schedule_cron,
            schedule_rrule,
            schedule_next_run_at,
            if schedule_enabled { 1 } else { 0 },
            ts,
            deployment_id,
        ],
    )
    .map_err(|e| e.to_string())?;

    conn.query_row(
        "SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,\
         concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,\
         schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at \
         FROM deployments WHERE id = ?1",
        params![deployment_id],
        |row| deployment_row_to_json(row),
    )
    .map_err(|e| e.to_string())
}

/// Fire due interval schedules: enqueue runs and advance `schedule_next_run_at`.
fn tick_interval_schedules(conn: &Connection) -> Result<u64, String> {
    let now = now_iso();
    let mut stmt = conn
        .prepare(
            "SELECT id, schedule_interval_seconds, schedule_next_run_at \
             FROM deployments \
             WHERE schedule_enabled = 1 AND paused = 0 \
             AND schedule_interval_seconds IS NOT NULL AND schedule_interval_seconds > 0 \
             AND schedule_next_run_at IS NOT NULL AND schedule_next_run_at <= ?1",
        )
        .map_err(|e| e.to_string())?;
    let ids: Vec<(String, i64)> = stmt
        .query_map(params![now], |row| Ok((row.get(0)?, row.get(1)?)))
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    drop(stmt);

    let mut fired: u64 = 0;
    for (dep_id, interval_sec) in ids {
        let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
        if let Err(e) = trigger_deployment_run_tx(&tx, &dep_id, Some(&json!({})), None) {
            tx.rollback().map_err(|e| e.to_string())?;
            if e == "deployment not found" {
                continue;
            }
            return Err(e);
        }
        let next = (Utc::now() + Duration::seconds(interval_sec)).to_rfc3339();
        let ts = now_iso();
        tx.execute(
            "UPDATE deployments SET schedule_next_run_at = ?1, updated_at = ?2 WHERE id = ?3",
            params![next, ts, dep_id],
        )
        .map_err(|e| e.to_string())?;
        tx.commit().map_err(|e| e.to_string())?;
        fired += 1;
    }
    Ok(fired)
}

/// Cron-based schedules (mutually exclusive with interval/RRule in application logic).
fn tick_cron_schedules(conn: &Connection) -> Result<u64, String> {
    let now = now_iso();
    let mut stmt = conn
        .prepare(
            "SELECT id, schedule_cron, schedule_next_run_at \
             FROM deployments \
             WHERE schedule_enabled = 1 AND paused = 0 \
             AND schedule_cron IS NOT NULL AND trim(schedule_cron) != '' \
             AND (schedule_rrule IS NULL OR trim(schedule_rrule) = '') \
             AND (schedule_interval_seconds IS NULL OR schedule_interval_seconds <= 0) \
             AND schedule_next_run_at IS NOT NULL AND schedule_next_run_at <= ?1",
        )
        .map_err(|e| e.to_string())?;
    let ids: Vec<(String, String)> = stmt
        .query_map(params![now], |row| Ok((row.get(0)?, row.get(1)?)))
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    drop(stmt);

    let mut fired: u64 = 0;
    for (dep_id, cron_expr) in ids {
        let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
        if let Err(e) = trigger_deployment_run_tx(&tx, &dep_id, Some(&json!({})), None) {
            tx.rollback().map_err(|e| e.to_string())?;
            if e == "deployment not found" {
                continue;
            }
            return Err(e);
        }
        let after = Utc::now();
        let next = next_cron_occurrence(&cron_expr, after)?.to_rfc3339();
        let ts = now_iso();
        tx.execute(
            "UPDATE deployments SET schedule_next_run_at = ?1, updated_at = ?2 WHERE id = ?3",
            params![next, ts, dep_id],
        )
        .map_err(|e| e.to_string())?;
        tx.commit().map_err(|e| e.to_string())?;
        fired += 1;
    }
    Ok(fired)
}

/// RRule-based schedules (small deterministic subset).
fn tick_rrule_schedules(conn: &Connection) -> Result<u64, String> {
    let now = now_iso();
    let mut stmt = conn
        .prepare(
            "SELECT id, schedule_rrule, schedule_next_run_at \
             FROM deployments \
             WHERE schedule_enabled = 1 AND paused = 0 \
             AND schedule_rrule IS NOT NULL AND trim(schedule_rrule) != '' \
             AND schedule_next_run_at IS NOT NULL AND schedule_next_run_at <= ?1",
        )
        .map_err(|e| e.to_string())?;
    let ids: Vec<(String, String)> = stmt
        .query_map(params![now], |row| Ok((row.get(0)?, row.get(1)?)))
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    drop(stmt);

    let mut fired: u64 = 0;
    for (dep_id, rrule_expr) in ids {
        let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
        if let Err(e) = trigger_deployment_run_tx(&tx, &dep_id, Some(&json!({})), None) {
            tx.rollback().map_err(|e| e.to_string())?;
            if e == "deployment not found" {
                continue;
            }
            return Err(e);
        }
        let next = next_rrule_occurrence(&rrule_expr, Utc::now())?.to_rfc3339();
        let ts = now_iso();
        tx.execute(
            "UPDATE deployments SET schedule_next_run_at = ?1, updated_at = ?2 WHERE id = ?3",
            params![next, ts, dep_id],
        )
        .map_err(|e| e.to_string())?;
        tx.commit().map_err(|e| e.to_string())?;
        fired += 1;
    }
    Ok(fired)
}

/// Fire due interval, cron, and RRule schedules.
pub fn tick_deployment_schedules(conn: &Connection) -> Result<u64, String> {
    let a = tick_interval_schedules(conn)?;
    let b = tick_cron_schedules(conn)?;
    let c = tick_rrule_schedules(conn)?;
    Ok(a + b + c)
}

pub fn mark_deployment_run_started(
    conn: &Connection,
    deployment_run_id: &str,
) -> Result<(), String> {
    let now = now_iso();
    conn.execute(
        "UPDATE deployment_runs SET status = 'RUNNING', started_at = ?1, updated_at = ?1 WHERE id = ?2",
        params![now, deployment_run_id],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn mark_deployment_run_finished(
    conn: &Connection,
    deployment_run_id: &str,
    status: &str,
    flow_run_id: Option<&str>,
    error: Option<&str>,
) -> Result<(), String> {
    let now = now_iso();
    conn.execute(
        "UPDATE deployment_runs SET status = ?1, flow_run_id = ?2, error = ?3, finished_at = ?4, updated_at = ?4, lease_until = NULL \
         WHERE id = ?5",
        params![status, flow_run_id, error, now, deployment_run_id],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// One FFI round-trip: reclaim leases, fire due schedules, mark stale workers offline.
pub fn deployment_maintenance(
    conn: &Connection,
    stale_after_seconds: i64,
) -> Result<Value, String> {
    let reclaimed = reclaim_expired_claims(conn)?;
    let triggered = tick_deployment_schedules(conn)?;
    let reaped = reap_stale_workers(conn, stale_after_seconds)?;
    Ok(json!({
        "reclaimed": reclaimed,
        "triggered": triggered,
        "reaped": reaped,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn create_deployment_sets_work_pool_id() {
        let conn = Connection::open_in_memory().expect("open db");
        conn.execute_batch(
            "CREATE TABLE deployments (
                id TEXT UNIQUE NOT NULL,
                name TEXT UNIQUE NOT NULL,
                flow_name TEXT NOT NULL,
                entrypoint TEXT,
                path TEXT,
                default_parameters TEXT NOT NULL,
                paused INTEGER NOT NULL,
                concurrency_limit INTEGER,
                collision_strategy TEXT NOT NULL DEFAULT 'ENQUEUE',
                schedule_interval_seconds INTEGER,
                schedule_cron TEXT,
                schedule_rrule TEXT,
                schedule_next_run_at TEXT,
                schedule_enabled INTEGER NOT NULL DEFAULT 0,
                work_pool_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );",
        )
        .expect("schema");

        let custom = create_deployment(
            &conn,
            &json!({
                "name": "pool-bound",
                "flow_name": "simple_flow",
                "work_pool_id": "custom-process-pool",
            }),
        )
        .expect("create with pool");
        assert_eq!(custom["work_pool_id"], "custom-process-pool");

        let default_pool = create_deployment(
            &conn,
            &json!({
                "name": "default-pool",
                "flow_name": "simple_flow",
            }),
        )
        .expect("create default pool");
        assert_eq!(default_pool["work_pool_id"], DEFAULT_WORK_POOL_ID);
    }

    #[test]
    fn next_cron_occurrence_advances() {
        let t0 = Utc::now();
        let t1 = next_cron_occurrence("0 * * * * *", t0).expect("parse");
        assert!(t1 > t0);
    }

    #[test]
    fn next_rrule_occurrence_advances() {
        let t0 = Utc::now();
        let t1 = next_rrule_occurrence("FREQ=MINUTELY;INTERVAL=5", t0).expect("parse");
        assert!(t1 > t0);
        assert_eq!(t1 - t0, Duration::minutes(5));
    }

    #[test]
    fn next_rrule_rejects_count() {
        let err =
            next_rrule_occurrence("FREQ=DAILY;COUNT=3", Utc::now()).expect_err("COUNT rejected");
        assert!(err.contains("COUNT"));
    }
}
