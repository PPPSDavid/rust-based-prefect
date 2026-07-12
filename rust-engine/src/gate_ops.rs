//! Gate task promotion — SQLite hot path for temporal barriers.
//! Called from `ironflow_control` when `bind_db` has attached a connection.

use std::str::FromStr;

use rusqlite::{params, Connection};
use serde_json::{json, Value};
use uuid::Uuid;

use crate::engine::{Engine, RunState, SetTaskStateRequest, TransitionStatus};
use crate::ui_write;

fn now_iso() -> String {
    chrono::Utc::now().to_rfc3339()
}

fn promote_gate_task(
    conn: &Connection,
    engine: &mut Engine,
    task_id: Uuid,
) -> Result<bool, String> {
    let task = match engine.get_task_run(task_id) {
        Some(t) => t.clone(),
        None => return Ok(false),
    };
    if task.state != RunState::Pending {
        return Ok(false);
    }

    let token_run = Uuid::new_v4();
    let run_resp = engine
        .set_task_state(SetTaskStateRequest {
            task_run_id: task_id,
            to_state: RunState::Running,
            expected_version: Some(task.version),
            transition_token: token_run,
            transition_kind: "task_running".to_string(),
        })
        .map_err(|e| format!("gate running: {e}"))?;
    ui_write::persist_task_transition_with_conn(
        conn,
        engine,
        task_id,
        "task_running",
        None,
        run_resp.status,
    )
    .map_err(|e| e.to_string())?;

    let task = engine
        .get_task_run(task_id)
        .ok_or_else(|| "gate task missing after running".to_string())?
        .clone();
    let token_done = Uuid::new_v4();
    let done_resp = engine
        .set_task_state(SetTaskStateRequest {
            task_run_id: task_id,
            to_state: RunState::Completed,
            expected_version: Some(task.version),
            transition_token: token_done,
            transition_kind: "task_completed".to_string(),
        })
        .map_err(|e| format!("gate completed: {e}"))?;
    ui_write::persist_task_transition_with_conn(
        conn,
        engine,
        task_id,
        "task_completed",
        None,
        done_resp.status,
    )
    .map_err(|e| e.to_string())?;
    Ok(run_resp.status == TransitionStatus::Applied
        || done_resp.status == TransitionStatus::Applied)
}

/// Promote gate tasks whose ``gate_open_at`` is due (PENDING → COMPLETED).
pub fn tick_gate_tasks(conn: &Connection, engine: &mut Engine) -> Result<u64, String> {
    let now = now_iso();
    let mut stmt = conn
        .prepare(
            "SELECT id FROM task_runs \
             WHERE kind = 'gate' AND state = 'PENDING' \
               AND gate_open_at IS NOT NULL AND gate_open_at <= ?1",
        )
        .map_err(|e| e.to_string())?;
    let ids: Vec<String> = stmt
        .query_map(params![now], |row| row.get(0))
        .map_err(|e| e.to_string())?
        .filter_map(|r| r.ok())
        .collect();

    let mut promoted = 0u64;
    for id_str in ids {
        let task_id = Uuid::from_str(&id_str).map_err(|e| format!("bad gate task id: {e}"))?;
        if promote_gate_task(conn, engine, task_id)? {
            promoted += 1;
        }
    }
    Ok(promoted)
}

/// Gate promotion result for FFI.
pub fn tick_gate_tasks_json(conn: &Connection, engine: &mut Engine) -> Result<Value, String> {
    let promoted = tick_gate_tasks(conn, engine)?;
    Ok(json!({"promoted": promoted}))
}
