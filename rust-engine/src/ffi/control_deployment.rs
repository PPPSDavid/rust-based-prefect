use serde_json::{json, Value};

use crate::concurrency_ops;
use crate::deployment_ops;
use crate::deployment_ops_pg;
use crate::gate_ops;

use super::helpers::pg_fallback;
use super::EngineContext;

pub(crate) fn handle(ctx: &mut EngineContext, op: &str, body: &Value) -> Result<Value, String> {
    match op {
        "deployment_create" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("deployment_create"));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_create requires bind_db".to_string())?;
            match deployment_ops::create_deployment(conn, body) {
                Ok(dep) => Ok(json!({"ok": true, "deployment": dep})),
                Err(e) => Ok(json!({"ok": false, "error": {"code": "deployment", "message": e}})),
            }
        }
        "deployment_update" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("deployment_update"));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_update requires bind_db".to_string())?;
            match deployment_ops::update_deployment(conn, body) {
                Ok(dep) => Ok(json!({"ok": true, "deployment": dep})),
                Err(e) => {
                    let code = if e == "deployment not found" {
                        "not_found"
                    } else {
                        "deployment"
                    };
                    Ok(json!({"ok": false, "error": {"code": code, "message": e}}))
                }
            }
        }
        "deployment_claim_next" => {
            let worker_name = body
                .get("worker_name")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field worker_name".to_string())?;
            let lease_seconds = body
                .get("lease_seconds")
                .and_then(|v| v.as_i64())
                .unwrap_or(30)
                .max(1);
            let work_pool_id = body.get("work_pool_id").and_then(|v| v.as_str());
            if let Some(client) = ctx.pg_client.as_mut() {
                match deployment_ops_pg::claim_next_deployment_run(
                    client,
                    worker_name,
                    lease_seconds,
                    work_pool_id,
                ) {
                    Ok(Some(run)) => Ok(json!({"ok": true, "run": run})),
                    Ok(None) => Ok(json!({"ok": true, "run": Value::Null})),
                    Err(e) => {
                        Ok(json!({"ok": false, "error": {"code": "deployment", "message": e}}))
                    }
                }
            } else {
                let conn = ctx.db_conn.as_ref().ok_or_else(|| {
                    "deployment_claim_next requires bind_db (shared SQLite connection)".to_string()
                })?;
                match deployment_ops::claim_next_deployment_run(
                    conn,
                    worker_name,
                    lease_seconds,
                    work_pool_id,
                ) {
                    Ok(Some(run)) => Ok(json!({"ok": true, "run": run})),
                    Ok(None) => Ok(json!({"ok": true, "run": Value::Null})),
                    Err(e) => {
                        Ok(json!({"ok": false, "error": {"code": "deployment", "message": e}}))
                    }
                }
            }
        }
        "deployment_claim_next_wait" => {
            // Keep this op non-blocking under the global engines() mutex.
            // Python handles the wait loop and calls deployment_claim_next repeatedly.
            let worker_name = body
                .get("worker_name")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field worker_name".to_string())?;
            let lease_seconds = body
                .get("lease_seconds")
                .and_then(|v| v.as_i64())
                .unwrap_or(30)
                .max(1);
            let work_pool_id = body.get("work_pool_id").and_then(|v| v.as_str());
            if let Some(client) = ctx.pg_client.as_mut() {
                match deployment_ops_pg::claim_next_deployment_run(
                    client,
                    worker_name,
                    lease_seconds,
                    work_pool_id,
                ) {
                    Ok(Some(run)) => Ok(json!({"ok": true, "run": run})),
                    Ok(None) => Ok(json!({"ok": true, "run": Value::Null})),
                    Err(e) => {
                        Ok(json!({"ok": false, "error": {"code": "deployment", "message": e}}))
                    }
                }
            } else {
                let conn = ctx
                    .db_conn
                    .as_ref()
                    .ok_or_else(|| "deployment_claim_next_wait requires bind_db".to_string())?;
                match deployment_ops::claim_next_deployment_run(
                    conn,
                    worker_name,
                    lease_seconds,
                    work_pool_id,
                ) {
                    Ok(Some(run)) => Ok(json!({"ok": true, "run": run})),
                    Ok(None) => Ok(json!({"ok": true, "run": Value::Null})),
                    Err(e) => {
                        Ok(json!({"ok": false, "error": {"code": "deployment", "message": e}}))
                    }
                }
            }
        }
        "deployment_trigger_run" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("deployment_trigger_run"));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_trigger_run requires bind_db".to_string())?;
            let deployment_id = body
                .get("deployment_id")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field deployment_id".to_string())?;
            let requested = body.get("parameters");
            let idempotency_key = body.get("idempotency_key").and_then(|v| v.as_str());
            let parent_link = deployment_ops::DeploymentParentLink {
                parent_flow_run_id: body
                    .get("parent_flow_run_id")
                    .and_then(|v| v.as_str())
                    .map(str::to_string),
                parent_task_run_id: body
                    .get("parent_task_run_id")
                    .and_then(|v| v.as_str())
                    .map(str::to_string),
                parent_deployment_run_id: body
                    .get("parent_deployment_run_id")
                    .and_then(|v| v.as_str())
                    .map(str::to_string),
            };
            let parent_ref = if parent_link.parent_flow_run_id.is_some()
                || parent_link.parent_task_run_id.is_some()
                || parent_link.parent_deployment_run_id.is_some()
            {
                Some(parent_link)
            } else {
                None
            };
            match deployment_ops::trigger_deployment_run(
                conn,
                deployment_id,
                requested,
                idempotency_key,
                parent_ref.as_ref(),
            ) {
                Ok(run) => Ok(json!({"ok": true, "run": run})),
                Err(e) => {
                    let code = if e == "deployment not found" {
                        "not_found"
                    } else if e == "deployment is paused" {
                        "paused"
                    } else {
                        "deployment"
                    };
                    Ok(json!({"ok": false, "error": {"code": code, "message": e}}))
                }
            }
        }
        "deployment_get_run" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("deployment_get_run"));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_get_run requires bind_db".to_string())?;
            let deployment_run_id = body
                .get("deployment_run_id")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field deployment_run_id".to_string())?;
            match deployment_ops::get_deployment_run(conn, deployment_run_id) {
                Ok(Some(run)) => Ok(json!({"ok": true, "run": run})),
                Ok(None) => Ok(json!({"ok": true, "run": Value::Null})),
                Err(e) => Ok(json!({"ok": false, "error": {"code": "deployment", "message": e}})),
            }
        }
        "deployment_cancel_by_parent_flow" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("deployment_cancel_by_parent_flow"));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_cancel_by_parent_flow requires bind_db".to_string())?;
            let parent_flow_run_id = body
                .get("parent_flow_run_id")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field parent_flow_run_id".to_string())?;
            match deployment_ops::cancel_deployment_runs_for_parent_flow(conn, parent_flow_run_id) {
                Ok(cancelled) => Ok(json!({"ok": true, "cancelled": cancelled})),
                Err(e) => Ok(json!({"ok": false, "error": {"code": "deployment", "message": e}})),
            }
        }
        "deployment_reclaim_expired" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("deployment_reclaim_expired"));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_reclaim_expired requires bind_db".to_string())?;
            let n = deployment_ops::reclaim_expired_claims(conn).map_err(|e| e.to_string())?;
            Ok(json!({"ok": true, "reclaimed": n}))
        }
        "deployment_worker_heartbeat" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("deployment_worker_heartbeat"));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_worker_heartbeat requires bind_db".to_string())?;
            let worker_name = body
                .get("worker_name")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field worker_name".to_string())?;
            let work_pool_id = body.get("work_pool_id").and_then(|v| v.as_str());
            deployment_ops::worker_heartbeat(conn, worker_name, work_pool_id)
                .map_err(|e| e.to_string())?;
            Ok(json!({"ok": true}))
        }
        "deployment_tick_schedules" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("deployment_tick_schedules"));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_tick_schedules requires bind_db".to_string())?;
            let n = deployment_ops::tick_deployment_schedules(conn).map_err(|e| e.to_string())?;
            Ok(json!({"ok": true, "triggered": n}))
        }
        "deployment_reap_stale_workers" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("deployment_reap_stale_workers"));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_reap_stale_workers requires bind_db".to_string())?;
            let stale = body
                .get("stale_after_seconds")
                .and_then(|v| v.as_i64())
                .unwrap_or(120)
                .max(1);
            let n = deployment_ops::reap_stale_workers(conn, stale).map_err(|e| e.to_string())?;
            Ok(json!({"ok": true, "reaped": n}))
        }
        "deployment_mark_run_started" => {
            let id = body
                .get("deployment_run_id")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field deployment_run_id".to_string())?;
            if let Some(client) = ctx.pg_client.as_mut() {
                deployment_ops_pg::mark_deployment_run_started(client, id)
                    .map_err(|e| e.to_string())?;
                return Ok(json!({"ok": true}));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_mark_run_started requires bind_db".to_string())?;
            deployment_ops::mark_deployment_run_started(conn, id).map_err(|e| e.to_string())?;
            Ok(json!({"ok": true}))
        }
        "deployment_attach_flow_run" => {
            let deployment_run_id = body
                .get("deployment_run_id")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field deployment_run_id".to_string())?;
            let flow_run_id = body
                .get("flow_run_id")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field flow_run_id".to_string())?;
            if let Some(client) = ctx.pg_client.as_mut() {
                deployment_ops_pg::attach_flow_run_to_deployment_run(
                    client,
                    deployment_run_id,
                    flow_run_id,
                )
                .map_err(|e| e.to_string())?;
                return Ok(json!({"ok": true}));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_attach_flow_run requires bind_db".to_string())?;
            deployment_ops::attach_flow_run_to_deployment_run(conn, deployment_run_id, flow_run_id)
                .map_err(|e| e.to_string())?;
            Ok(json!({"ok": true}))
        }
        "deployment_mark_run_finished" => {
            let id = body
                .get("deployment_run_id")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field deployment_run_id".to_string())?;
            let status = body
                .get("status")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field status".to_string())?;
            let flow_run_id = body.get("flow_run_id").and_then(|v| v.as_str());
            let error = body.get("error").and_then(|v| v.as_str());
            if let Some(client) = ctx.pg_client.as_mut() {
                deployment_ops_pg::mark_deployment_run_finished(
                    client,
                    id,
                    status,
                    flow_run_id,
                    error,
                )
                .map_err(|e| e.to_string())?;
                return Ok(json!({"ok": true}));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_mark_run_finished requires bind_db".to_string())?;
            deployment_ops::mark_deployment_run_finished(conn, id, status, flow_run_id, error)
                .map_err(|e| e.to_string())?;
            Ok(json!({"ok": true}))
        }
        "deployment_maintenance" => {
            // On Postgres: reclaim + reap in Rust; schedule/gate ticks use Python fallback.
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("deployment_maintenance"));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_maintenance requires bind_db".to_string())?;
            let stale = body
                .get("stale_after_seconds")
                .and_then(|v| v.as_i64())
                .unwrap_or(120)
                .max(1);
            let mut summary =
                deployment_ops::deployment_maintenance(conn, stale).map_err(|e| e.to_string())?;
            let gates =
                gate_ops::tick_gate_tasks(conn, &mut ctx.engine).map_err(|e| e.to_string())?;
            let gcl_reclaimed =
                concurrency_ops::reclaim_expired(conn, None).map_err(|e| e.to_string())?;
            if let Some(obj) = summary.as_object_mut() {
                obj.insert("gates_promoted".to_string(), json!(gates));
                obj.insert("gcl_reclaimed".to_string(), json!(gcl_reclaimed));
            }
            Ok(json!({"ok": true, "summary": summary}))
        }
        "catalog_retention_sweep" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("catalog_retention_sweep"));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "catalog_retention_sweep requires bind_db".to_string())?;
            let cutoff = body.get("cutoff").and_then(|v| v.as_str());
            let gc_orphans = body
                .get("gc_orphans")
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            match crate::flow_catalog_ops::catalog_retention_sweep(conn, cutoff, gc_orphans) {
                Ok(summary) => Ok(
                    json!({"ok": true, "deleted_runs": summary.get("deleted_runs").cloned().unwrap_or(json!(0)), "gc_flows": summary.get("gc_flows").cloned().unwrap_or(json!(0)), "summary": summary}),
                ),
                Err(e) => Ok(json!({"ok": false, "error": {"code": "catalog", "message": e}})),
            }
        }
        "task_tick_gate_tasks" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("task_tick_gate_tasks"));
            }
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "task_tick_gate_tasks requires bind_db".to_string())?;
            let promoted =
                gate_ops::tick_gate_tasks(conn, &mut ctx.engine).map_err(|e| e.to_string())?;
            Ok(json!({"ok": true, "promoted": promoted}))
        }
        _ => Err(format!("unknown control op: {op}")),
    }
}
