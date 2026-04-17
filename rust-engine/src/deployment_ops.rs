//! Deployment queue, scheduling, and worker heartbeats — SQLite hot paths kept in Rust.
//! Called from `ironflow_control` when `bind_db` has attached a connection.

use chrono::{Duration, Utc};
use rusqlite::{params, Connection, OptionalExtension, Result as SqlResult};
use serde_json::{json, Value};
use uuid::Uuid;

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

fn deployment_run_row_to_json(row: &rusqlite::Row) -> SqlResult<Value> {
    let requested: Value = serde_json::from_str(row.get::<_, String>("requested_parameters")?.as_str())
        .unwrap_or(json!({}));
    let resolved: Value = serde_json::from_str(row.get::<_, String>("resolved_parameters")?.as_str())
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
pub fn worker_heartbeat(conn: &Connection, worker_name: &str) -> Result<(), String> {
    let now = now_iso();
    conn.execute(
        "INSERT INTO workers(name,last_heartbeat,status,updated_at) VALUES(?1,?2,'ONLINE',?3) \
         ON CONFLICT(name) DO UPDATE SET last_heartbeat = excluded.last_heartbeat, \
         status = excluded.status, updated_at = excluded.updated_at",
        params![worker_name, now, now],
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
) -> Result<Option<Value>, String> {
    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    worker_heartbeat(&tx, worker_name)?;
    reclaim_expired_claims(&tx)?;

    let now_dt = Utc::now();
    let now = now_dt.to_rfc3339();
    let lease_until = (now_dt + Duration::seconds(lease_seconds.max(1))).to_rfc3339();

    let candidate_id: Option<String> = tx
        .query_row(
            "SELECT dr.id FROM deployment_runs dr \
             INNER JOIN deployments d ON d.id = dr.deployment_id \
             WHERE dr.status = 'SCHEDULED' \
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
            [],
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

    let Some((dep_id, default_parameters, paused, concurrency_limit, collision_strategy)) = dep else {
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

/// Fire due interval schedules: enqueue runs and advance `schedule_next_run_at`.
pub fn tick_deployment_schedules(conn: &Connection) -> Result<u64, String> {
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

pub fn mark_deployment_run_started(conn: &Connection, deployment_run_id: &str) -> Result<(), String> {
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
pub fn deployment_maintenance(conn: &Connection, stale_after_seconds: i64) -> Result<Value, String> {
    let reclaimed = reclaim_expired_claims(conn)?;
    let triggered = tick_deployment_schedules(conn)?;
    let reaped = reap_stale_workers(conn, stale_after_seconds)?;
    Ok(json!({
        "reclaimed": reclaimed,
        "triggered": triggered,
        "reaped": reaped,
    }))
}
