use chrono::{Duration, Utc};
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};

use super::claim::reclaim_expired_claims;
use super::rows::{deployment_run_row_to_json, now_iso};
use super::tick::tick_deployment_schedules;

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

pub fn attach_flow_run_to_deployment_run(
    conn: &Connection,
    deployment_run_id: &str,
    flow_run_id: &str,
) -> Result<(), String> {
    let now = now_iso();
    conn.execute(
        "UPDATE deployment_runs SET flow_run_id = ?1, updated_at = ?2 \
         WHERE id = ?3 AND (flow_run_id IS NULL OR flow_run_id = ?1)",
        params![flow_run_id, now, deployment_run_id],
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

/// Cancel active deployment runs enqueued as subflows of ``parent_flow_run_id``.
/// Returns JSON array of cancelled run summaries (id, flow_run_id, parent_task_run_id).
pub fn cancel_deployment_runs_for_parent_flow(
    conn: &Connection,
    parent_flow_run_id: &str,
) -> Result<Vec<Value>, String> {
    let now = now_iso();
    let mut stmt = conn
        .prepare(
            "SELECT id,flow_run_id,parent_task_run_id FROM deployment_runs \
             WHERE parent_flow_run_id = ?1 AND status IN ('SCHEDULED','CLAIMED','RUNNING')",
        )
        .map_err(|e| e.to_string())?;
    let targets: Vec<(String, Option<String>, Option<String>)> = stmt
        .query_map(params![parent_flow_run_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
                row.get::<_, Option<String>>(2)?,
            ))
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    if targets.is_empty() {
        return Ok(Vec::new());
    }
    conn.execute(
        "UPDATE deployment_runs SET status = 'CANCELLED', error = 'parent flow cancelled', \
         finished_at = ?1, updated_at = ?1, lease_until = NULL \
         WHERE parent_flow_run_id = ?2 AND status IN ('SCHEDULED','CLAIMED','RUNNING')",
        params![now, parent_flow_run_id],
    )
    .map_err(|e| e.to_string())?;
    Ok(targets
        .into_iter()
        .map(|(id, flow_run_id, parent_task_run_id)| {
            json!({
                "id": id,
                "flow_run_id": flow_run_id,
                "parent_task_run_id": parent_task_run_id,
                "status": "CANCELLED",
            })
        })
        .collect())
}

/// Read a single deployment run row (hot path for subflow wait polling).
pub fn get_deployment_run(
    conn: &Connection,
    deployment_run_id: &str,
) -> Result<Option<Value>, String> {
    conn.query_row(
        "SELECT id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,\
         worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,\
         created_at,updated_at,started_at,finished_at \
         FROM deployment_runs WHERE id = ?1 LIMIT 1",
        params![deployment_run_id],
        deployment_run_row_to_json,
    )
    .optional()
    .map_err(|e| e.to_string())
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
