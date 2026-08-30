use rusqlite::Connection;
use serde_json::{json, Value};
use std::time::Duration;

use crate::engine::{FlowRun, SetStateRequest, SetTaskStateRequest, TaskRun};
use crate::ui_write;

use super::helpers::{
    engine_error_value, opt_str_from_field, pg_fallback, resolve_db_path, set_state_response_json,
    state_from_field, u64_from_field, uuid_from_field,
};
use super::EngineContext;

pub(crate) fn handle(ctx: &mut EngineContext, op: &str, body: &Value) -> Result<Value, String> {
    match op {
        "bind_db" => {
            if let Some(url) = body.get("database_url").and_then(|v| v.as_str()) {
                let url = url.to_string();
                if !(url.starts_with("postgres://") || url.starts_with("postgresql://")) {
                    return Err(
                        "database_url must be a postgres:// or postgresql:// DSN".to_string()
                    );
                }
                let client =
                    postgres::Client::connect(&url, postgres::NoTls).map_err(|e| e.to_string())?;
                ctx.db_path = Some(url);
                ctx.db_conn = None;
                ctx.pg_client = Some(client);
                return Ok(json!({"ok": true, "backend": "postgres"}));
            }
            let db_path = body
                .get("db_path")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing string field db_path or database_url".to_string())?
                .to_string();
            let conn = Connection::open(&db_path).map_err(|e| e.to_string())?;
            conn.busy_timeout(Duration::from_millis(5_000))
                .map_err(|e| e.to_string())?;
            conn.execute_batch("PRAGMA journal_mode=WAL;")
                .map_err(|e| e.to_string())?;
            ctx.db_path = Some(db_path);
            ctx.db_conn = Some(conn);
            ctx.pg_client = None;
            Ok(json!({"ok": true, "backend": "sqlite"}))
        }
        "register_flow" => {
            let run: FlowRun = serde_json::from_value(body.clone()).map_err(|e| e.to_string())?;
            ctx.engine.register_flow_run(run);
            Ok(json!({"ok": true}))
        }
        "create_flow_run_persist" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("create_flow_run_persist"));
            }
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
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("create_task_run_persist"));
            }
            let db_path = resolve_db_path(ctx, body)?;
            let task: TaskRun =
                serde_json::from_value(body.get("task").cloned().unwrap_or_else(|| body.clone()))
                    .map_err(|e| e.to_string())?;
            let planned_node_id = opt_str_from_field(body, "planned_node_id");
            let kind = opt_str_from_field(body, "kind");
            let child_flow_run_id = opt_str_from_field(body, "child_flow_run_id");
            let child_deployment_run_id = opt_str_from_field(body, "child_deployment_run_id");
            let gate_open_at = opt_str_from_field(body, "gate_open_at");
            let contribute_to_flow_state = body
                .get("contribute_to_flow_state")
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            ctx.engine.register_task_run(task.clone());
            if let Some(conn) = ctx.db_conn.as_ref() {
                ui_write::persist_task_create_with_conn(
                    conn,
                    &task,
                    planned_node_id.as_deref(),
                    kind.as_deref(),
                    child_flow_run_id.as_deref(),
                    child_deployment_run_id.as_deref(),
                    gate_open_at.as_deref(),
                    contribute_to_flow_state,
                )
                .map_err(|e| format!("persist task create failed: {e}"))?;
            } else {
                ui_write::persist_task_create(
                    &db_path,
                    &task,
                    planned_node_id.as_deref(),
                    kind.as_deref(),
                    child_flow_run_id.as_deref(),
                    child_deployment_run_id.as_deref(),
                    gate_open_at.as_deref(),
                    contribute_to_flow_state,
                )
                .map_err(|e| format!("persist task create failed: {e}"))?;
            }
            Ok(json!({"ok": true}))
        }
        "save_flow_manifest_persist" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("save_flow_manifest_persist"));
            }
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
            let req: SetStateRequest =
                serde_json::from_value(body.clone()).map_err(|e| e.to_string())?;
            match ctx.engine.set_flow_state(req) {
                Ok(resp) => Ok(set_state_response_json(&resp)),
                Err(e) => Ok(json!({"ok": false, "error": engine_error_value(e)})),
            }
        }
        "set_flow_state_persist" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("set_flow_state_persist"));
            }
            let db_path = resolve_db_path(ctx, body)?;
            let req: SetStateRequest = serde_json::from_value(
                body.get("request").cloned().unwrap_or_else(|| body.clone()),
            )
            .map_err(|e| e.to_string())?;
            let run_id = req.run_id;
            match ctx.engine.set_flow_state(req) {
                Ok(resp) => {
                    let persist_res = if let Some(conn) = ctx.db_conn.as_ref() {
                        ui_write::persist_flow_transition_with_conn(
                            conn,
                            &ctx.engine,
                            run_id,
                            resp.status,
                        )
                    } else {
                        ui_write::persist_flow_transition(
                            &db_path,
                            &ctx.engine,
                            run_id,
                            resp.status,
                        )
                    };
                    if let Err(e) = persist_res {
                        return Ok(
                            json!({"ok": false, "error": {"code": "persistence", "message": e}}),
                        );
                    }
                    Ok(set_state_response_json(&resp))
                }
                Err(e) => Ok(json!({"ok": false, "error": engine_error_value(e)})),
            }
        }
        "set_flow_states_persist_batch" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("set_flow_states_persist_batch"));
            }
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
                            if let Err(e) = ui_write::persist_flow_transition_with_conn(
                                &tx,
                                &ctx.engine,
                                run_id,
                                resp.status,
                            ) {
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
                            if let Err(e) = ui_write::persist_flow_transition_with_conn(
                                &tx,
                                &ctx.engine,
                                run_id,
                                resp.status,
                            ) {
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
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("set_task_state_persist"));
            }
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
                        return Ok(
                            json!({"ok": false, "error": {"code": "persistence", "message": e}}),
                        );
                    }
                    Ok(set_state_response_json(&resp))
                }
                Err(e) => Ok(json!({"ok": false, "error": engine_error_value(e)})),
            }
        }
        "set_task_states_persist_batch" => {
            if ctx.pg_client.is_some() {
                return Ok(pg_fallback("set_task_states_persist_batch"));
            }
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
        _ => Err(format!("unknown control op: {op}")),
    }
}
