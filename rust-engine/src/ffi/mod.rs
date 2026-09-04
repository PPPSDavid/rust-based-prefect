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
use crate::engine::Engine;
use crate::ui_read;

fn cstr_to_string(ptr: *const c_char) -> Result<String, String> {
    if ptr.is_null() {
        return Err("received null pointer".to_string());
    }
    let cstr = unsafe { CStr::from_ptr(ptr) };
    cstr.to_str()
        .map(|s| s.to_string())
        .map_err(|e| e.to_string())
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
#[allow(clippy::not_unsafe_ptr_arg_deref)] // C ABI: ctypes cannot call `unsafe fn`
pub extern "C" fn ironflow_free_string(ptr: *mut c_char) {
    if ptr.is_null() {
        return;
    }
    unsafe {
        let _ = CString::from_raw(ptr);
    }
}

pub(crate) struct EngineContext {
    pub(crate) engine: Engine,
    pub(crate) db_path: Option<String>,
    pub(crate) db_conn: Option<Connection>,
    /// When set, hot-path claim/lease ops use Postgres instead of SQLite.
    pub(crate) pg_client: Option<postgres::Client>,
}

fn engines() -> &'static Mutex<HashMap<u64, Arc<Mutex<EngineContext>>>> {
    static CELL: OnceLock<Mutex<HashMap<u64, Arc<Mutex<EngineContext>>>>> = OnceLock::new();
    CELL.get_or_init(|| Mutex::new(HashMap::new()))
}

fn engine_context(handle: u64) -> Result<Arc<Mutex<EngineContext>>, String> {
    if handle == 0 {
        return Err("invalid engine handle 0".to_string());
    }
    let map = engines()
        .lock()
        .map_err(|_| "engine map poisoned".to_string())?;
    map.get(&handle)
        .cloned()
        .ok_or_else(|| format!("unknown engine handle {handle}"))
}

struct DeploymentSchedulerHandle {
    stop: Arc<AtomicBool>,
    join: Option<thread::JoinHandle<()>>,
}

static DEPLOYMENT_SCHEDULERS: OnceLock<Mutex<HashMap<u64, DeploymentSchedulerHandle>>> =
    OnceLock::new();

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

mod control_deployment;
mod control_fsm;
mod control_gcl;
mod control_terminal;
pub(crate) mod helpers;

fn dispatch_control(ctx: &mut EngineContext, op: &str, body: &Value) -> Result<Value, String> {
    match op {
        "bind_db"
        | "register_flow"
        | "create_flow_run_persist"
        | "register_task"
        | "create_task_run_persist"
        | "save_flow_manifest_persist"
        | "apply_flow_checkpoint"
        | "apply_task_checkpoint"
        | "set_flow_state"
        | "set_flow_state_persist"
        | "set_flow_states_persist_batch"
        | "set_task_state"
        | "set_task_state_persist"
        | "set_task_states_persist_batch" => control_fsm::handle(ctx, op, body),
        "deployment_create"
        | "deployment_update"
        | "deployment_claim_next"
        | "deployment_claim_next_wait"
        | "deployment_trigger_run"
        | "deployment_get_run"
        | "deployment_cancel_by_parent_flow"
        | "deployment_reclaim_expired"
        | "deployment_worker_heartbeat"
        | "deployment_tick_schedules"
        | "deployment_reap_stale_workers"
        | "deployment_mark_run_started"
        | "deployment_attach_flow_run"
        | "deployment_mark_run_finished"
        | "deployment_maintenance"
        | "catalog_retention_sweep"
        | "ensure_flow_canonical"
        | "task_tick_gate_tasks" => control_deployment::handle(ctx, op, body),
        op if op.starts_with("gcl_") => control_gcl::handle(ctx, op, body),
        "resolve_flow_terminal_state" | "list_contributing_children" => {
            control_terminal::handle(ctx, op, body)
        }
        _ => Err(format!("unknown control op: {op}")),
    }
}

/// Opaque control-plane engine handle (per Python ``InMemoryControlPlane``). Handle ``0`` is invalid.
#[no_mangle]
pub extern "C" fn ironflow_engine_new() -> u64 {
    let h = NEXT_ENGINE_HANDLE.fetch_add(1, Ordering::Relaxed);
    let ctx = Arc::new(Mutex::new(EngineContext {
        engine: Engine::new(),
        db_path: None,
        db_conn: None,
        pg_client: None,
    }));
    engines()
        .lock()
        .expect("engine map poisoned")
        .insert(h, ctx);
    h
}

#[no_mangle]
pub extern "C" fn ironflow_engine_free(handle: u64) {
    if handle == 0 {
        return;
    }
    ironflow_deployment_scheduler_stop_internal(handle);
    engines()
        .lock()
        .expect("engine map poisoned")
        .remove(&handle);
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
            let Ok(ctx_arc) = engine_context(handle) else {
                break;
            };
            let Ok(ctx) = ctx_arc.lock() else {
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
        let op = cstr_to_string(op)?;
        let json_in = cstr_to_string(json_in)?;
        let body: Value = if json_in.trim().is_empty() {
            json!({})
        } else {
            serde_json::from_str(&json_in).map_err(|e| e.to_string())?
        };
        let ctx_arc = engine_context(handle)?;
        let mut ctx = ctx_arc
            .lock()
            .map_err(|_| "engine context poisoned".to_string())?;
        match dispatch_control(&mut ctx, &op, &body) {
            Ok(v) => Ok(v.to_string()),
            Err(e) => {
                Ok(json!({"ok": false, "error": {"code": "dispatch", "message": e}}).to_string())
            }
        }
    })();

    match result {
        Ok(s) => CString::new(s).unwrap_or_default().into_raw(),
        Err(e) => {
            let payload = format!(
                r#"{{"ok":false,"error":{{"code":"ffi","message":"{}"}}}}"#,
                e.replace('"', "\\\"")
            );
            CString::new(payload).unwrap_or_default().into_raw()
        }
    }
}
