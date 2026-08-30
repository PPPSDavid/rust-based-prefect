use chrono::{Duration, Utc};
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};
use uuid::Uuid;

use super::rows::{deployment_run_row_to_json, merge_parameters, now_iso, DEFAULT_WORK_POOL_ID};

pub fn worker_heartbeat(
    conn: &Connection,
    worker_name: &str,
    work_pool_id: Option<&str>,
) -> Result<(), String> {
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
             INNER JOIN work_pools wp ON wp.id = COALESCE(d.work_pool_id, 'default-process-pool') AND wp.paused = 0 \
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
             worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,\
             created_at,updated_at,started_at,finished_at \
             FROM deployment_runs WHERE id = ?1 AND status = 'CLAIMED'",
            params![cid],
            deployment_run_row_to_json,
        )
        .map_err(|e| e.to_string())?;
    tx.commit().map_err(|e| e.to_string())?;
    Ok(Some(row))
}

/// Optional parent linkage when a deployment run is triggered as a subflow task.
#[derive(Debug, Clone, Default)]
pub struct DeploymentParentLink {
    pub parent_flow_run_id: Option<String>,
    pub parent_task_run_id: Option<String>,
    pub parent_deployment_run_id: Option<String>,
}

/// Insert a deployment run row (SCHEDULED or CANCELLED for CANCEL_NEW at capacity).
/// Runs entirely inside `tx` (no nested transaction).
pub fn trigger_deployment_run_tx(
    tx: &Connection,
    deployment_id: &str,
    requested: Option<&Value>,
    idempotency_key: Option<&str>,
    parent_link: Option<&DeploymentParentLink>,
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
                     worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,\
                     created_at,updated_at,started_at,finished_at \
                     FROM deployment_runs WHERE id = ?1",
                    params![rid],
                    deployment_run_row_to_json,
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
    let parent_flow_run_id = parent_link.and_then(|p| p.parent_flow_run_id.as_deref());
    let parent_task_run_id = parent_link.and_then(|p| p.parent_task_run_id.as_deref());
    let parent_deployment_run_id = parent_link.and_then(|p| p.parent_deployment_run_id.as_deref());
    tx.execute(
        "INSERT INTO deployment_runs \
         (id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,\
          worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,\
          created_at,updated_at,started_at,finished_at) \
         VALUES (?1,?2,?3,?4,?5,?6,NULL,NULL,NULL,?7,?8,?9,?10,?11,?12,NULL,NULL)",
        params![
            run_id,
            dep_id,
            status,
            requested_str,
            resolved_str,
            idempotency_key,
            error,
            parent_flow_run_id,
            parent_task_run_id,
            parent_deployment_run_id,
            now,
            now,
        ],
    )
    .map_err(|e| e.to_string())?;

    tx.query_row(
        "SELECT id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,\
         worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,\
         created_at,updated_at,started_at,finished_at \
         FROM deployment_runs WHERE id = ?1",
        params![run_id],
        deployment_run_row_to_json,
    )
    .map_err(|e| e.to_string())
}

pub fn trigger_deployment_run(
    conn: &Connection,
    deployment_id: &str,
    requested: Option<&Value>,
    idempotency_key: Option<&str>,
    parent_link: Option<&DeploymentParentLink>,
) -> Result<Value, String> {
    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    let v = trigger_deployment_run_tx(&tx, deployment_id, requested, idempotency_key, parent_link)?;
    tx.commit().map_err(|e| e.to_string())?;
    Ok(v)
}
