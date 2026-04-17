use chrono::Utc;
use rusqlite::{params, Connection};
use serde_json::Value;
use uuid::Uuid;

use crate::engine::{Engine, TransitionStatus};

fn now_iso() -> String {
    Utc::now().to_rfc3339()
}

pub fn persist_flow_create(db_path: &str, run: &crate::engine::FlowRun) -> Result<(), String> {
    let conn = Connection::open(db_path).map_err(|e| e.to_string())?;
    persist_flow_create_with_conn(&conn, run)
}

pub fn persist_flow_create_with_conn(
    conn: &Connection,
    run: &crate::engine::FlowRun,
) -> Result<(), String> {
    let ts = now_iso();
    conn.execute(
        "INSERT OR IGNORE INTO flow_runs(id,name,state,version,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        params![
            run.id.to_string(),
            run.name.as_str(),
            format!("{:?}", run.state).to_uppercase(),
            run.version as i64,
            ts,
            now_iso()
        ],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn persist_task_create(
    db_path: &str,
    task: &crate::engine::TaskRun,
    planned_node_id: Option<&str>,
) -> Result<(), String> {
    let conn = Connection::open(db_path).map_err(|e| e.to_string())?;
    persist_task_create_with_conn(&conn, task, planned_node_id)
}

pub fn persist_task_create_with_conn(
    conn: &Connection,
    task: &crate::engine::TaskRun,
    planned_node_id: Option<&str>,
) -> Result<(), String> {
    let ts = now_iso();
    conn.execute(
        "INSERT OR IGNORE INTO task_runs(id,flow_run_id,task_name,planned_node_id,state,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        params![
            task.id.to_string(),
            task.flow_run_id.to_string(),
            task.task_key.as_str(),
            planned_node_id,
            format!("{:?}", task.state).to_uppercase(),
            task.version as i64,
            ts,
            now_iso()
        ],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn persist_flow_manifest(
    db_path: &str,
    flow_run_id: Uuid,
    manifest_json: &str,
    forecast_json: &str,
    warnings_json: &str,
    fallback_required: bool,
    source: &str,
) -> Result<(), String> {
    let conn = Connection::open(db_path).map_err(|e| e.to_string())?;
    persist_flow_manifest_with_conn(
        &conn,
        flow_run_id,
        manifest_json,
        forecast_json,
        warnings_json,
        fallback_required,
        source,
    )
}

pub fn persist_flow_manifest_with_conn(
    conn: &Connection,
    flow_run_id: Uuid,
    manifest_json: &str,
    forecast_json: &str,
    warnings_json: &str,
    fallback_required: bool,
    source: &str,
) -> Result<(), String> {
    conn.execute(
        "INSERT OR REPLACE INTO dag_manifests (flow_run_id, manifest_json, forecast_json, warnings_json, fallback_required, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        params![
            flow_run_id.to_string(),
            manifest_json,
            forecast_json,
            warnings_json,
            if fallback_required { 1_i64 } else { 0_i64 },
            source,
            now_iso()
        ],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn persist_flow_transition(
    db_path: &str,
    engine: &Engine,
    run_id: Uuid,
    status: TransitionStatus,
) -> Result<(), String> {
    let conn = Connection::open(db_path).map_err(|e| e.to_string())?;
    persist_flow_transition_with_conn(&conn, engine, run_id, status)
}

pub fn persist_flow_transition_with_conn(
    conn: &Connection,
    engine: &Engine,
    run_id: Uuid,
    status: TransitionStatus,
) -> Result<(), String> {
    if status != TransitionStatus::Applied {
        return Ok(());
    }
    let run = engine
        .get_flow_run(run_id)
        .ok_or_else(|| format!("flow run not found: {run_id}"))?;
    let ev = engine
        .event_log()
        .last()
        .ok_or_else(|| "missing flow transition event".to_string())?;
    if ev.task_run_id.is_some() || ev.run_id != run_id {
        return Ok(());
    }
    let ts = now_iso();
    conn.execute(
        "UPDATE flow_runs SET state = ?, version = ?, updated_at = ? WHERE id = ?",
        params![
            format!("{:?}", run.state).to_uppercase(),
            run.version as i64,
            ts,
            run_id.to_string()
        ],
    )
    .map_err(|e| e.to_string())?;
    conn.execute(
        "INSERT OR IGNORE INTO events(event_id,run_id,task_run_id,from_state,to_state,event_type,kind,data,timestamp) VALUES(?,?,?,?,?,?,?,?,?)",
        params![
            ev.event_id.to_string(),
            run_id.to_string(),
            Option::<String>::None,
            format!("{:?}", ev.from_state).to_uppercase(),
            format!("{:?}", ev.to_state).to_uppercase(),
            Option::<String>::None,
            ev.transition_kind,
            "{}",
            ts
        ],
    )
    .map_err(|e| e.to_string())?;
    conn.execute(
        "INSERT INTO logs(id,flow_run_id,task_run_id,level,message,timestamp) VALUES(?,?,?,?,?,?)",
        params![
            Uuid::new_v4().to_string(),
            run_id.to_string(),
            Option::<String>::None,
            "INFO",
            format!(
                "Flow state transition {} -> {}",
                format!("{:?}", ev.from_state).to_uppercase(),
                format!("{:?}", ev.to_state).to_uppercase()
            ),
            now_iso()
        ],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn persist_task_transition(
    db_path: &str,
    engine: &Engine,
    task_run_id: Uuid,
    event_type: &str,
    data: Option<&Value>,
    status: TransitionStatus,
) -> Result<(), String> {
    let conn = Connection::open(db_path).map_err(|e| e.to_string())?;
    persist_task_transition_with_conn(&conn, engine, task_run_id, event_type, data, status)
}

pub fn persist_task_transition_with_conn(
    conn: &Connection,
    engine: &Engine,
    task_run_id: Uuid,
    event_type: &str,
    data: Option<&Value>,
    status: TransitionStatus,
) -> Result<(), String> {
    if status != TransitionStatus::Applied {
        return Ok(());
    }
    let task = engine
        .get_task_run(task_run_id)
        .ok_or_else(|| format!("task run not found: {task_run_id}"))?;
    let ev = engine
        .event_log()
        .last()
        .ok_or_else(|| "missing task transition event".to_string())?;
    if ev.task_run_id != Some(task_run_id) {
        return Ok(());
    }
    let ts = now_iso();
    conn.execute(
        "UPDATE task_runs SET state = ?, version = ?, updated_at = ? WHERE id = ?",
        params![
            format!("{:?}", task.state).to_uppercase(),
            task.version as i64,
            ts,
            task_run_id.to_string()
        ],
    )
    .map_err(|e| e.to_string())?;
    conn.execute(
        "INSERT OR IGNORE INTO events(event_id,run_id,task_run_id,from_state,to_state,event_type,kind,data,timestamp) VALUES(?,?,?,?,?,?,?,?,?)",
        params![
            ev.event_id.to_string(),
            task.flow_run_id.to_string(),
            task_run_id.to_string(),
            format!("{:?}", ev.from_state).to_uppercase(),
            format!("{:?}", ev.to_state).to_uppercase(),
            event_type,
            Option::<String>::None,
            data.cloned().unwrap_or_else(|| Value::Object(Default::default())).to_string(),
            ts
        ],
    )
    .map_err(|e| e.to_string())?;
    let lvl = if event_type == "task_failed" { "ERROR" } else { "INFO" };
    conn.execute(
        "INSERT INTO logs(id,flow_run_id,task_run_id,level,message,timestamp) VALUES(?,?,?,?,?,?)",
        params![
            Uuid::new_v4().to_string(),
            task.flow_run_id.to_string(),
            task_run_id.to_string(),
            lvl,
            format!("{}: {}", task.task_key, event_type),
            now_iso()
        ],
    )
    .map_err(|e| e.to_string())?;
    if event_type == "task_completed" {
        conn.execute(
            "INSERT INTO artifacts(id,flow_run_id,task_run_id,artifact_type,key,summary,created_at) VALUES(?,?,?,?,?,?,?)",
            params![
                Uuid::new_v4().to_string(),
                task.flow_run_id.to_string(),
                task_run_id.to_string(),
                "result",
                format!("{}-result", task.task_key),
                data.cloned().unwrap_or_else(|| Value::Object(Default::default())).to_string(),
                now_iso(),
            ],
        )
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}
