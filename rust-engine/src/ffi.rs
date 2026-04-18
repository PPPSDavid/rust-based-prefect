use std::collections::HashMap;
use std::ffi::{CStr, CString};
use std::os::raw::c_char;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::Duration;

use rusqlite::Connection;
use serde_json::{json, Value};

use crate::deployment_ops;
use crate::engine::{
    Engine, EngineError, FlowRun, SetStateRequest, SetTaskStateRequest, TaskRun,
};
use crate::ui_read;
use crate::ui_write;

fn cstr_to_string(ptr: *const c_char) -> Result<String, String> {
    if ptr.is_null() {
        return Err("received null pointer".to_string());
    }
    let cstr = unsafe { CStr::from_ptr(ptr) };
    cstr.to_str().map(|s| s.to_string()).map_err(|e| e.to_string())
}

#[no_mangle]
pub extern "C" fn ironflow_query(
    db_path: *const c_char,
    kind: *const c_char,
    params_json: *const c_char,
) -> *mut c_char {
    let result = (|| -> Result<String, String> {
        let db_path = cstr_to_string(db_path)?;
        let kind = cstr_to_string(kind)?;
        let params_json = cstr_to_string(params_json)?;
        ui_read::query(&db_path, &kind, &params_json)
    })();

    match result {
        Ok(s) => CString::new(s).unwrap_or_default().into_raw(),
        Err(e) => {
            let payload = format!(r#"{{"error":"{}"}}"#, e.replace('"', "\\\""));
            CString::new(payload).unwrap_or_default().into_raw()
        }
    }
}

#[no_mangle]
pub extern "C" fn ironflow_free_string(ptr: *mut c_char) {
    if ptr.is_null() {
        return;
    }
    unsafe {
        let _ = CString::from_raw(ptr);
    }
}

struct EngineContext {
    engine: Engine,
    db_path: Option<String>,
    db_conn: Option<Connection>,
}

fn engines() -> &'static Mutex<HashMap<u64, EngineContext>> {
    static CELL: OnceLock<Mutex<HashMap<u64, EngineContext>>> = OnceLock::new();
    CELL.get_or_init(|| Mutex::new(HashMap::new()))
}

struct DeploymentSchedulerHandle {
    stop: Arc<AtomicBool>,
    join: Option<thread::JoinHandle<()>>,
}

static DEPLOYMENT_SCHEDULERS: OnceLock<Mutex<HashMap<u64, DeploymentSchedulerHandle>>> = OnceLock::new();

fn deployment_schedulers() -> &'static Mutex<HashMap<u64, DeploymentSchedulerHandle>> {
    DEPLOYMENT_SCHEDULERS.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Stop background scheduler thread for `handle` (no-op if none).
fn ironflow_deployment_scheduler_stop_internal(handle: u64) {
    if handle == 0 {
        return;
    }
    let Ok(mut map) = deployment_schedulers().lock() else {
        return;
    };
    if let Some(mut h) = map.remove(&handle) {
        h.stop.store(true, Ordering::SeqCst);
        if let Some(j) = h.join.take() {
            let _ = j.join();
        }
    }
}

static NEXT_ENGINE_HANDLE: AtomicU64 = AtomicU64::new(1);

fn engine_error_value(err: EngineError) -> Value {
    match err {
        EngineError::MissingRun(id) => json!({
            "code": "missing_run",
            "message": err.to_string(),
            "run_id": id.to_string(),
        }),
        EngineError::MissingTask(id) => json!({
            "code": "missing_task",
            "message": err.to_string(),
            "task_run_id": id.to_string(),
        }),
        EngineError::InvalidTransition { from, to } => json!({
            "code": "invalid_transition",
            "message": err.to_string(),
            "from": from,
            "to": to,
        }),
        EngineError::VersionConflict { expected, actual } => json!({
            "code": "version_conflict",
            "message": err.to_string(),
            "expected": expected,
            "actual": actual,
        }),
    }
}

fn set_state_response_json(resp: &crate::engine::SetStateResponse) -> Value {
    let status = match resp.status {
        crate::engine::TransitionStatus::Applied => "applied",
        crate::engine::TransitionStatus::Duplicate => "duplicate",
    };
    json!({
        "ok": true,
        "status": status,
        "current_state": resp.current_state,
        "version": resp.version,
    })
}

fn resolve_db_path(ctx: &EngineContext, body: &Value) -> Result<String, String> {
    if let Some(path) = body.get("db_path").and_then(|v| v.as_str()) {
        return Ok(path.to_string());
    }
    ctx.db_path
        .clone()
        .ok_or_else(|| "missing db path (call bind_db or provide db_path)".to_string())
}

fn dispatch_control(ctx: &mut EngineContext, op: &str, body: &Value) -> Result<Value, String> {
    match op {
        "bind_db" => {
            let db_path = body
                .get("db_path")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field db_path".to_string())?
                .to_string();
            let conn = Connection::open(&db_path).map_err(|e| e.to_string())?;
            ctx.db_path = Some(db_path);
            ctx.db_conn = Some(conn);
            Ok(json!({"ok": true}))
        }
        "register_flow" => {
            let run: FlowRun = serde_json::from_value(body.clone()).map_err(|e| e.to_string())?;
            ctx.engine.register_flow_run(run);
            Ok(json!({"ok": true}))
        }
        "create_flow_run_persist" => {
            let db_path = resolve_db_path(ctx, body)?;
            let run: FlowRun =
                serde_json::from_value(body.get("run").cloned().unwrap_or_else(|| body.clone()))
                    .map_err(|e| e.to_string())?;
            ctx.engine.register_flow_run(run.clone());
            if let Some(conn) = ctx.db_conn.as_ref() {
                ui_write::persist_flow_create_with_conn(conn, &run)
                    .map_err(|e| format!("persist flow create failed: {e}"))?;
            } else {
                ui_write::persist_flow_create(&db_path, &run)
                    .map_err(|e| format!("persist flow create failed: {e}"))?;
            }
            Ok(json!({"ok": true}))
        }
        "register_task" => {
            let task: TaskRun = serde_json::from_value(body.clone()).map_err(|e| e.to_string())?;
            ctx.engine.register_task_run(task);
            Ok(json!({"ok": true}))
        }
        "create_task_run_persist" => {
            let db_path = resolve_db_path(ctx, body)?;
            let task: TaskRun =
                serde_json::from_value(body.get("task").cloned().unwrap_or_else(|| body.clone()))
                    .map_err(|e| e.to_string())?;
            let planned_node_id = opt_str_from_field(body, "planned_node_id");
            ctx.engine.register_task_run(task.clone());
            if let Some(conn) = ctx.db_conn.as_ref() {
                ui_write::persist_task_create_with_conn(conn, &task, planned_node_id.as_deref())
                    .map_err(|e| format!("persist task create failed: {e}"))?;
            } else {
                ui_write::persist_task_create(&db_path, &task, planned_node_id.as_deref())
                    .map_err(|e| format!("persist task create failed: {e}"))?;
            }
            Ok(json!({"ok": true}))
        }
        "save_flow_manifest_persist" => {
            let db_path = resolve_db_path(ctx, body)?;
            let flow_run_id = uuid_from_field(body, "flow_run_id")?;
            let manifest_json = body
                .get("manifest_json")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field manifest_json".to_string())?;
            let forecast_json = body
                .get("forecast_json")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field forecast_json".to_string())?;
            let warnings_json = body
                .get("warnings_json")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field warnings_json".to_string())?;
            let fallback_required = body
                .get("fallback_required")
                .and_then(|v| v.as_bool())
                .ok_or_else(|| "missing bool field fallback_required".to_string())?;
            let source = body
                .get("source")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field source".to_string())?;
            if let Some(conn) = ctx.db_conn.as_ref() {
                ui_write::persist_flow_manifest_with_conn(
                    conn,
                    flow_run_id,
                    manifest_json,
                    forecast_json,
                    warnings_json,
                    fallback_required,
                    source,
                )
                .map_err(|e| format!("persist flow manifest failed: {e}"))?;
            } else {
                ui_write::persist_flow_manifest(
                    &db_path,
                    flow_run_id,
                    manifest_json,
                    forecast_json,
                    warnings_json,
                    fallback_required,
                    source,
                )
                .map_err(|e| format!("persist flow manifest failed: {e}"))?;
            }
            Ok(json!({"ok": true}))
        }
        "apply_flow_checkpoint" => {
            let run_id = uuid_from_field(body, "run_id")?;
            let state = state_from_field(body, "state")?;
            let version = u64_from_field(body, "version")?;
            ctx.engine
                .apply_flow_checkpoint(run_id, state, version)
                .map_err(|e| e.to_string())?;
            Ok(json!({"ok": true}))
        }
        "apply_task_checkpoint" => {
            let task_run_id = uuid_from_field(body, "task_run_id")?;
            let state = state_from_field(body, "state")?;
            let version = u64_from_field(body, "version")?;
            ctx.engine
                .apply_task_checkpoint(task_run_id, state, version)
                .map_err(|e| e.to_string())?;
            Ok(json!({"ok": true}))
        }
        "set_flow_state" => {
            let req: SetStateRequest = serde_json::from_value(body.clone()).map_err(|e| e.to_string())?;
            match ctx.engine.set_flow_state(req) {
                Ok(resp) => Ok(set_state_response_json(&resp)),
                Err(e) => Ok(json!({"ok": false, "error": engine_error_value(e)})),
            }
        }
        "set_flow_state_persist" => {
            let db_path = resolve_db_path(ctx, body)?;
            let req: SetStateRequest =
                serde_json::from_value(body.get("request").cloned().unwrap_or_else(|| body.clone()))
                    .map_err(|e| e.to_string())?;
            let run_id = req.run_id;
            match ctx.engine.set_flow_state(req) {
                Ok(resp) => {
                    let persist_res = if let Some(conn) = ctx.db_conn.as_ref() {
                        ui_write::persist_flow_transition_with_conn(conn, &ctx.engine, run_id, resp.status)
                    } else {
                        ui_write::persist_flow_transition(&db_path, &ctx.engine, run_id, resp.status)
                    };
                    if let Err(e) = persist_res {
                        return Ok(json!({"ok": false, "error": {"code": "persistence", "message": e}}));
                    }
                    Ok(set_state_response_json(&resp))
                }
                Err(e) => Ok(json!({"ok": false, "error": engine_error_value(e)})),
            }
        }
        "set_flow_states_persist_batch" => {
            let db_path = resolve_db_path(ctx, body)?;
            let items = body
                .get("items")
                .and_then(|v| v.as_array())
                .ok_or_else(|| "missing array field items".to_string())?;
            let mut out_results: Vec<Value> = Vec::with_capacity(items.len());
            if let Some(conn) = ctx.db_conn.as_mut() {
                let tx = conn.transaction().map_err(|e| e.to_string())?;
                for (idx, item) in items.iter().enumerate() {
                    let req: SetStateRequest = serde_json::from_value(
                        item.get("request")
                            .cloned()
                            .ok_or_else(|| format!("missing request at index {idx}"))?,
                    )
                    .map_err(|e| format!("invalid request at index {idx}: {e}"))?;
                    let run_id = req.run_id;
                    match ctx.engine.set_flow_state(req) {
                        Ok(resp) => {
                            if let Err(e) =
                                ui_write::persist_flow_transition_with_conn(&tx, &ctx.engine, run_id, resp.status)
                            {
                                return Ok(json!({
                                    "ok": false,
                                    "error": {"code": "persistence", "message": e},
                                    "index": idx
                                }));
                            }
                            out_results.push(set_state_response_json(&resp));
                        }
                        Err(e) => {
                            return Ok(json!({
                                "ok": false,
                                "error": engine_error_value(e),
                                "index": idx
                            }));
                        }
                    }
                }
                tx.commit().map_err(|e| e.to_string())?;
            } else {
                let mut conn = Connection::open(&db_path).map_err(|e| e.to_string())?;
                let tx = conn.transaction().map_err(|e| e.to_string())?;
                for (idx, item) in items.iter().enumerate() {
                    let req: SetStateRequest = serde_json::from_value(
                        item.get("request")
                            .cloned()
                            .ok_or_else(|| format!("missing request at index {idx}"))?,
                    )
                    .map_err(|e| format!("invalid request at index {idx}: {e}"))?;
                    let run_id = req.run_id;
                    match ctx.engine.set_flow_state(req) {
                        Ok(resp) => {
                            if let Err(e) =
                                ui_write::persist_flow_transition_with_conn(&tx, &ctx.engine, run_id, resp.status)
                            {
                                return Ok(json!({
                                    "ok": false,
                                    "error": {"code": "persistence", "message": e},
                                    "index": idx
                                }));
                            }
                            out_results.push(set_state_response_json(&resp));
                        }
                        Err(e) => {
                            return Ok(json!({
                                "ok": false,
                                "error": engine_error_value(e),
                                "index": idx
                            }));
                        }
                    }
                }
                tx.commit().map_err(|e| e.to_string())?;
            }
            Ok(json!({"ok": true, "results": out_results}))
        }
        "set_task_state" => {
            let req: SetTaskStateRequest =
                serde_json::from_value(body.clone()).map_err(|e| e.to_string())?;
            match ctx.engine.set_task_state(req) {
                Ok(resp) => Ok(set_state_response_json(&resp)),
                Err(e) => Ok(json!({"ok": false, "error": engine_error_value(e)})),
            }
        }
        "set_task_state_persist" => {
            let db_path = resolve_db_path(ctx, body)?;
            let event_type = body
                .get("event_type")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field event_type".to_string())?;
            let data = body.get("data");
            let req: SetTaskStateRequest = serde_json::from_value(
                body.get("request").cloned().unwrap_or_else(|| body.clone()),
            )
            .map_err(|e| e.to_string())?;
            let task_run_id = req.task_run_id;
            match ctx.engine.set_task_state(req) {
                Ok(resp) => {
                    let persist_res = if let Some(conn) = ctx.db_conn.as_ref() {
                        ui_write::persist_task_transition_with_conn(
                            conn,
                            &ctx.engine,
                            task_run_id,
                            event_type,
                            data,
                            resp.status,
                        )
                    } else {
                        ui_write::persist_task_transition(
                            &db_path,
                            &ctx.engine,
                            task_run_id,
                            event_type,
                            data,
                            resp.status,
                        )
                    };
                    if let Err(e) = persist_res {
                        return Ok(json!({"ok": false, "error": {"code": "persistence", "message": e}}));
                    }
                    Ok(set_state_response_json(&resp))
                }
                Err(e) => Ok(json!({"ok": false, "error": engine_error_value(e)})),
            }
        }
        "set_task_states_persist_batch" => {
            let db_path = resolve_db_path(ctx, body)?;
            let items = body
                .get("items")
                .and_then(|v| v.as_array())
                .ok_or_else(|| "missing array field items".to_string())?;
            let mut out_results: Vec<Value> = Vec::with_capacity(items.len());
            if let Some(conn) = ctx.db_conn.as_mut() {
                let tx = conn.transaction().map_err(|e| e.to_string())?;
                for (idx, item) in items.iter().enumerate() {
                    let event_type = item
                        .get("event_type")
                        .and_then(|v| v.as_str())
                        .ok_or_else(|| format!("missing event_type at index {idx}"))?;
                    let data = item.get("data");
                    let req: SetTaskStateRequest = serde_json::from_value(
                        item.get("request")
                            .cloned()
                            .ok_or_else(|| format!("missing request at index {idx}"))?,
                    )
                    .map_err(|e| format!("invalid request at index {idx}: {e}"))?;
                    let task_run_id = req.task_run_id;
                    match ctx.engine.set_task_state(req) {
                        Ok(resp) => {
                            let persist_res = ui_write::persist_task_transition_with_conn(
                                &tx,
                                &ctx.engine,
                                task_run_id,
                                event_type,
                                data,
                                resp.status,
                            );
                            if let Err(e) = persist_res {
                                return Ok(json!({
                                    "ok": false,
                                    "error": {"code": "persistence", "message": e},
                                    "index": idx
                                }));
                            }
                            out_results.push(set_state_response_json(&resp));
                        }
                        Err(e) => {
                            return Ok(json!({
                                "ok": false,
                                "error": engine_error_value(e),
                                "index": idx
                            }));
                        }
                    }
                }
                tx.commit().map_err(|e| e.to_string())?;
            } else {
                let mut conn = Connection::open(&db_path).map_err(|e| e.to_string())?;
                let tx = conn.transaction().map_err(|e| e.to_string())?;
                for (idx, item) in items.iter().enumerate() {
                    let event_type = item
                        .get("event_type")
                        .and_then(|v| v.as_str())
                        .ok_or_else(|| format!("missing event_type at index {idx}"))?;
                    let data = item.get("data");
                    let req: SetTaskStateRequest = serde_json::from_value(
                        item.get("request")
                            .cloned()
                            .ok_or_else(|| format!("missing request at index {idx}"))?,
                    )
                    .map_err(|e| format!("invalid request at index {idx}: {e}"))?;
                    let task_run_id = req.task_run_id;
                    match ctx.engine.set_task_state(req) {
                        Ok(resp) => {
                            let persist_res = ui_write::persist_task_transition_with_conn(
                                &tx,
                                &ctx.engine,
                                task_run_id,
                                event_type,
                                data,
                                resp.status,
                            );
                            if let Err(e) = persist_res {
                                return Ok(json!({
                                    "ok": false,
                                    "error": {"code": "persistence", "message": e},
                                    "index": idx
                                }));
                            }
                            out_results.push(set_state_response_json(&resp));
                        }
                        Err(e) => {
                            return Ok(json!({
                                "ok": false,
                                "error": engine_error_value(e),
                                "index": idx
                            }));
                        }
                    }
                }
                tx.commit().map_err(|e| e.to_string())?;
            }
            Ok(json!({"ok": true, "results": out_results}))
        }
        "deployment_create" => {
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
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_claim_next requires bind_db (shared SQLite connection)".to_string())?;
            let worker_name = body
                .get("worker_name")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field worker_name".to_string())?;
            let lease_seconds = body.get("lease_seconds").and_then(|v| v.as_i64()).unwrap_or(30).max(1);
            match deployment_ops::claim_next_deployment_run(conn, worker_name, lease_seconds) {
                Ok(Some(run)) => Ok(json!({"ok": true, "run": run})),
                Ok(None) => Ok(json!({"ok": true, "run": Value::Null})),
                Err(e) => Ok(json!({"ok": false, "error": {"code": "deployment", "message": e}})),
            }
        }
        "deployment_claim_next_wait" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_claim_next_wait requires bind_db".to_string())?;
            let worker_name = body
                .get("worker_name")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field worker_name".to_string())?;
            let lease_seconds = body.get("lease_seconds").and_then(|v| v.as_i64()).unwrap_or(30).max(1);
            let wait_ms = body.get("wait_ms").and_then(|v| v.as_u64()).unwrap_or(500);
            match deployment_ops::claim_next_deployment_run_wait(conn, worker_name, lease_seconds, wait_ms) {
                Ok(Some(run)) => Ok(json!({"ok": true, "run": run})),
                Ok(None) => Ok(json!({"ok": true, "run": Value::Null})),
                Err(e) => Ok(json!({"ok": false, "error": {"code": "deployment", "message": e}})),
            }
        }
        "deployment_trigger_run" => {
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
            match deployment_ops::trigger_deployment_run(conn, deployment_id, requested, idempotency_key) {
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
        "deployment_reclaim_expired" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_reclaim_expired requires bind_db".to_string())?;
            let n = deployment_ops::reclaim_expired_claims(conn).map_err(|e| e.to_string())?;
            Ok(json!({"ok": true, "reclaimed": n}))
        }
        "deployment_worker_heartbeat" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_worker_heartbeat requires bind_db".to_string())?;
            let worker_name = body
                .get("worker_name")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field worker_name".to_string())?;
            deployment_ops::worker_heartbeat(conn, worker_name).map_err(|e| e.to_string())?;
            Ok(json!({"ok": true}))
        }
        "deployment_tick_schedules" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_tick_schedules requires bind_db".to_string())?;
            let n = deployment_ops::tick_deployment_schedules(conn).map_err(|e| e.to_string())?;
            Ok(json!({"ok": true, "triggered": n}))
        }
        "deployment_reap_stale_workers" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_reap_stale_workers requires bind_db".to_string())?;
            let stale = body.get("stale_after_seconds").and_then(|v| v.as_i64()).unwrap_or(120).max(1);
            let n = deployment_ops::reap_stale_workers(conn, stale).map_err(|e| e.to_string())?;
            Ok(json!({"ok": true, "reaped": n}))
        }
        "deployment_mark_run_started" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_mark_run_started requires bind_db".to_string())?;
            let id = body
                .get("deployment_run_id")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field deployment_run_id".to_string())?;
            deployment_ops::mark_deployment_run_started(conn, id).map_err(|e| e.to_string())?;
            Ok(json!({"ok": true}))
        }
        "deployment_mark_run_finished" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_mark_run_finished requires bind_db".to_string())?;
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
            deployment_ops::mark_deployment_run_finished(conn, id, status, flow_run_id, error)
                .map_err(|e| e.to_string())?;
            Ok(json!({"ok": true}))
        }
        "deployment_maintenance" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "deployment_maintenance requires bind_db".to_string())?;
            let stale = body.get("stale_after_seconds").and_then(|v| v.as_i64()).unwrap_or(120).max(1);
            let summary = deployment_ops::deployment_maintenance(conn, stale).map_err(|e| e.to_string())?;
            Ok(json!({"ok": true, "summary": summary}))
        }
        _ => Err(format!("unknown control op: {op}")),
    }
}

fn uuid_from_field(body: &Value, key: &str) -> Result<uuid::Uuid, String> {
    let s = body
        .get(key)
        .and_then(|v| v.as_str())
        .ok_or_else(|| format!("missing string field {key}"))?;
    uuid::Uuid::parse_str(s).map_err(|e| e.to_string())
}

fn u64_from_field(body: &Value, key: &str) -> Result<u64, String> {
    body.get(key)
        .and_then(|v| v.as_u64())
        .ok_or_else(|| format!("missing u64 field {key}"))
}

fn state_from_field(body: &Value, key: &str) -> Result<crate::engine::RunState, String> {
    serde_json::from_value(
        body.get(key)
            .cloned()
            .ok_or_else(|| format!("missing field {key}"))?,
    )
    .map_err(|e| e.to_string())
}

fn opt_str_from_field(body: &Value, key: &str) -> Option<String> {
    body.get(key).and_then(|v| v.as_str()).map(|s| s.to_string())
}

/// Opaque control-plane engine handle (per Python ``InMemoryControlPlane``). Handle ``0`` is invalid.
#[no_mangle]
pub extern "C" fn ironflow_engine_new() -> u64 {
    let h = NEXT_ENGINE_HANDLE.fetch_add(1, Ordering::Relaxed);
    engines().lock().expect("engine map poisoned").insert(
        h,
        EngineContext {
            engine: Engine::new(),
            db_path: None,
            db_conn: None,
        },
    );
    h
}

#[no_mangle]
pub extern "C" fn ironflow_engine_free(handle: u64) {
    if handle == 0 {
        return;
    }
    ironflow_deployment_scheduler_stop_internal(handle);
    engines().lock().expect("engine map poisoned").remove(&handle);
}

/// Spawn a background thread that periodically runs `deployment_maintenance` under the engine mutex.
#[no_mangle]
pub extern "C" fn ironflow_deployment_scheduler_start(
    handle: u64,
    interval_ms: u64,
    stale_after_seconds: i64,
) -> bool {
    if handle == 0 {
        return false;
    }
    ironflow_deployment_scheduler_stop_internal(handle);
    let stop = Arc::new(AtomicBool::new(false));
    let stop_t = Arc::clone(&stop);
    let sleep_ms = interval_ms.max(50);
    let stale = stale_after_seconds.max(1);
    let join = thread::spawn(move || {
        while !stop_t.load(Ordering::SeqCst) {
            thread::sleep(Duration::from_millis(sleep_ms));
            if stop_t.load(Ordering::SeqCst) {
                break;
            }
            let mut map = match engines().lock() {
                Ok(m) => m,
                Err(_) => break,
            };
            let Some(ctx) = map.get_mut(&handle) else {
                break;
            };
            let Some(conn) = ctx.db_conn.as_ref() else {
                continue;
            };
            let _ = deployment_ops::deployment_maintenance(conn, stale);
        }
    });
    if let Ok(mut m) = deployment_schedulers().lock() {
        m.insert(
            handle,
            DeploymentSchedulerHandle {
                stop,
                join: Some(join),
            },
        );
    }
    true
}

#[no_mangle]
pub extern "C" fn ironflow_deployment_scheduler_stop(handle: u64) {
    ironflow_deployment_scheduler_stop_internal(handle);
}

/// JSON in / JSON out control dispatch (FSM transitions, registration, replay checkpoints).
/// Response is either ``{"ok":true,...}`` or ``{"ok":false,"error":{...}}``.
#[no_mangle]
pub extern "C" fn ironflow_control(
    handle: u64,
    op: *const c_char,
    json_in: *const c_char,
) -> *mut c_char {
    let result = (|| -> Result<String, String> {
        if handle == 0 {
            return Err("invalid engine handle 0".to_string());
        }
        let op = cstr_to_string(op)?;
        let json_in = cstr_to_string(json_in)?;
        let body: Value = if json_in.trim().is_empty() {
            json!({})
        } else {
            serde_json::from_str(&json_in).map_err(|e| e.to_string())?
        };
        let mut map = engines().lock().map_err(|_| "engine map poisoned".to_string())?;
        let engine = map
            .get_mut(&handle)
            .ok_or_else(|| format!("unknown engine handle {handle}"))?;
        match dispatch_control(engine, &op, &body) {
            Ok(v) => Ok(v.to_string()),
            Err(e) => Ok(json!({"ok": false, "error": {"code": "dispatch", "message": e}}).to_string()),
        }
    })();

    match result {
        Ok(s) => CString::new(s).unwrap_or_default().into_raw(),
        Err(e) => {
            let payload = format!(r#"{{"ok":false,"error":{{"code":"ffi","message":"{}"}}}}"#, e.replace('"', "\\\""));
            CString::new(payload).unwrap_or_default().into_raw()
        }
    }
}
