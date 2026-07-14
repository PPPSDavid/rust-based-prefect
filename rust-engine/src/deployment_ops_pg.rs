//! Postgres ports of deployment claim / lease hot paths.
//! Called from `ironflow_control` when `bind_db` attached a Postgres client.

use chrono::{Duration, Utc};
use postgres::Client;
use serde_json::{json, Value};

const DEFAULT_WORK_POOL_ID: &str = "default-process-pool";

fn now_iso() -> String {
    Utc::now().to_rfc3339()
}

fn deployment_run_row_to_json(row: &postgres::Row) -> Value {
    let requested: Value = serde_json::from_str(row.get::<_, String>("requested_parameters").as_str())
        .unwrap_or(json!({}));
    let resolved: Value = serde_json::from_str(row.get::<_, String>("resolved_parameters").as_str())
        .unwrap_or(json!({}));
    json!({
        "id": row.get::<_, String>("id"),
        "deployment_id": row.get::<_, String>("deployment_id"),
        "status": row.get::<_, String>("status"),
        "requested_parameters": requested,
        "resolved_parameters": resolved,
        "idempotency_key": row.get::<_, Option<String>>("idempotency_key"),
        "worker_name": row.get::<_, Option<String>>("worker_name"),
        "lease_until": row.get::<_, Option<String>>("lease_until"),
        "flow_run_id": row.get::<_, Option<String>>("flow_run_id"),
        "error": row.get::<_, Option<String>>("error"),
        "parent_flow_run_id": row.get::<_, Option<String>>("parent_flow_run_id"),
        "parent_task_run_id": row.get::<_, Option<String>>("parent_task_run_id"),
        "parent_deployment_run_id": row.get::<_, Option<String>>("parent_deployment_run_id"),
        "created_at": row.get::<_, String>("created_at"),
        "updated_at": row.get::<_, String>("updated_at"),
        "started_at": row.get::<_, Option<String>>("started_at"),
        "finished_at": row.get::<_, Option<String>>("finished_at"),
    })
}

pub fn reclaim_expired_claims(client: &mut Client) -> Result<u64, String> {
    let now = now_iso();
    let n = client
        .execute(
            "UPDATE deployment_runs SET status = 'SCHEDULED', worker_name = NULL, lease_until = NULL, updated_at = $1 \
             WHERE status = 'CLAIMED' AND lease_until IS NOT NULL AND lease_until < $1",
            &[&now],
        )
        .map_err(|e| e.to_string())?;
    Ok(n)
}

pub fn worker_heartbeat(
    client: &mut Client,
    worker_name: &str,
    work_pool_id: Option<&str>,
) -> Result<(), String> {
    let now = now_iso();
    let pool = work_pool_id.map(str::to_string);
    client
        .execute(
            "INSERT INTO workers(name,last_heartbeat,status,updated_at,work_pool_id) \
             VALUES($1,$2,'ONLINE',$3,$4) \
             ON CONFLICT(name) DO UPDATE SET last_heartbeat = EXCLUDED.last_heartbeat, \
             status = EXCLUDED.status, updated_at = EXCLUDED.updated_at, \
             work_pool_id = COALESCE(EXCLUDED.work_pool_id, workers.work_pool_id)",
            &[&worker_name, &now, &now, &pool],
        )
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn reap_stale_workers(client: &mut Client, stale_after_seconds: i64) -> Result<u64, String> {
    let now = Utc::now();
    let cutoff = (now - Duration::seconds(stale_after_seconds)).to_rfc3339();
    let ts = now.to_rfc3339();
    let n = client
        .execute(
            "UPDATE workers SET status = 'OFFLINE', updated_at = $1 \
             WHERE status = 'ONLINE' AND last_heartbeat < $2",
            &[&ts, &cutoff],
        )
        .map_err(|e| e.to_string())?;
    Ok(n)
}

/// Claim the next eligible SCHEDULED run using ``FOR UPDATE SKIP LOCKED``.
pub fn claim_next_deployment_run(
    client: &mut Client,
    worker_name: &str,
    lease_seconds: i64,
    work_pool_id: Option<&str>,
) -> Result<Option<Value>, String> {
    let mut tx = client.transaction().map_err(|e| e.to_string())?;
    let now = now_iso();
    let pool = work_pool_id.map(str::to_string);
    tx.execute(
        "INSERT INTO workers(name,last_heartbeat,status,updated_at,work_pool_id) \
         VALUES($1,$2,'ONLINE',$3,$4) \
         ON CONFLICT(name) DO UPDATE SET last_heartbeat = EXCLUDED.last_heartbeat, \
         status = EXCLUDED.status, updated_at = EXCLUDED.updated_at, \
         work_pool_id = COALESCE(EXCLUDED.work_pool_id, workers.work_pool_id)",
        &[&worker_name, &now, &now, &pool],
    )
    .map_err(|e| e.to_string())?;

    tx.execute(
        "UPDATE deployment_runs SET status = 'SCHEDULED', worker_name = NULL, lease_until = NULL, updated_at = $1 \
         WHERE status = 'CLAIMED' AND lease_until IS NOT NULL AND lease_until < $1",
        &[&now],
    )
    .map_err(|e| e.to_string())?;

    let now_dt = Utc::now();
    let lease_until = (now_dt + Duration::seconds(lease_seconds.max(1))).to_rfc3339();
    let pool_filter = work_pool_id.unwrap_or(DEFAULT_WORK_POOL_ID).to_string();
    let default_pool = DEFAULT_WORK_POOL_ID.to_string();

    let candidate = tx
        .query_opt(
            "SELECT dr.id FROM deployment_runs dr \
             INNER JOIN deployments d ON d.id = dr.deployment_id \
             INNER JOIN work_pools wp ON wp.id = COALESCE(d.work_pool_id, 'default-process-pool') AND wp.paused = 0 \
             WHERE dr.status = 'SCHEDULED' \
             AND COALESCE(d.work_pool_id, $2) = $1 \
             AND ( \
               d.concurrency_limit IS NULL \
               OR ( \
                 SELECT COUNT(*) FROM deployment_runs x \
                 WHERE x.deployment_id = dr.deployment_id \
                 AND x.status IN ('CLAIMED','RUNNING') \
               ) < d.concurrency_limit \
             ) \
             ORDER BY dr.created_at ASC \
             LIMIT 1 \
             FOR UPDATE OF dr SKIP LOCKED",
            &[&pool_filter, &default_pool],
        )
        .map_err(|e| e.to_string())?;

    let Some(cand_row) = candidate else {
        tx.commit().map_err(|e| e.to_string())?;
        return Ok(None);
    };
    let cid: String = cand_row.get(0);
    let claim_now = now_iso();
    let updated = tx
        .execute(
            "UPDATE deployment_runs SET status = 'CLAIMED', worker_name = $1, lease_until = $2, updated_at = $3 \
             WHERE id = $4 AND status = 'SCHEDULED'",
            &[&worker_name, &lease_until, &claim_now, &cid],
        )
        .map_err(|e| e.to_string())?;
    if updated == 0 {
        tx.commit().map_err(|e| e.to_string())?;
        return Ok(None);
    }

    let row = tx
        .query_one(
            "SELECT id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,\
             worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,\
             created_at,updated_at,started_at,finished_at \
             FROM deployment_runs WHERE id = $1 AND status = 'CLAIMED'",
            &[&cid],
        )
        .map_err(|e| e.to_string())?;
    let out = deployment_run_row_to_json(&row);
    tx.commit().map_err(|e| e.to_string())?;
    Ok(Some(out))
}

pub fn mark_deployment_run_started(
    client: &mut Client,
    deployment_run_id: &str,
) -> Result<(), String> {
    let now = now_iso();
    client
        .execute(
            "UPDATE deployment_runs SET status = 'RUNNING', started_at = $1, updated_at = $1 WHERE id = $2",
            &[&now, &deployment_run_id],
        )
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn attach_flow_run_to_deployment_run(
    client: &mut Client,
    deployment_run_id: &str,
    flow_run_id: &str,
) -> Result<(), String> {
    let now = now_iso();
    client
        .execute(
            "UPDATE deployment_runs SET flow_run_id = $1, updated_at = $2 \
             WHERE id = $3 AND (flow_run_id IS NULL OR flow_run_id = $1)",
            &[&flow_run_id, &now, &deployment_run_id],
        )
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn mark_deployment_run_finished(
    client: &mut Client,
    deployment_run_id: &str,
    status: &str,
    flow_run_id: Option<&str>,
    error: Option<&str>,
) -> Result<(), String> {
    let now = now_iso();
    let flow = flow_run_id.map(str::to_string);
    let err = error.map(str::to_string);
    client
        .execute(
            "UPDATE deployment_runs SET status = $1, flow_run_id = $2, error = $3, finished_at = $4, updated_at = $4, lease_until = NULL \
             WHERE id = $5",
            &[&status, &flow, &err, &now, &deployment_run_id],
        )
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Lease reclaim + stale worker reap (schedule ticks fall back to Python on PG).
#[allow(dead_code)]
pub fn deployment_maintenance_lease(
    client: &mut Client,
    stale_after_seconds: i64,
) -> Result<Value, String> {
    let reclaimed = reclaim_expired_claims(client)?;
    let reaped = reap_stale_workers(client, stale_after_seconds)?;
    Ok(json!({
        "reclaimed": reclaimed,
        "triggered": 0u64,
        "reaped": reaped,
    }))
}
