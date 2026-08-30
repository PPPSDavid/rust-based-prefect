use rusqlite::Result as SqlResult;
use serde_json::{json, Value};

use chrono::Utc;

pub(crate) const DEFAULT_WORK_POOL_ID: &str = "default-process-pool";

pub(crate) fn now_iso() -> String {
    Utc::now().to_rfc3339()
}

pub(crate) fn merge_parameters(
    default_parameters: &str,
    requested: Option<&Value>,
) -> Result<String, String> {
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

pub(crate) fn deployment_row_to_json(row: &rusqlite::Row) -> SqlResult<Value> {
    let default_parameters: Value =
        serde_json::from_str(row.get::<_, String>("default_parameters")?.as_str())
            .unwrap_or(json!({}));
    Ok(json!({
        "id": row.get::<_, String>("id")?,
        "name": row.get::<_, String>("name")?,
        "flow_name": row.get::<_, String>("flow_name")?,
        "entrypoint": row.get::<_, Option<String>>("entrypoint")?,
        "path": row.get::<_, Option<String>>("path")?,
        "default_parameters": default_parameters,
        "paused": row.get::<_, i64>("paused")? != 0,
        "concurrency_limit": row.get::<_, Option<i64>>("concurrency_limit")?,
        "collision_strategy": row.get::<_, Option<String>>("collision_strategy")?.unwrap_or_else(|| "ENQUEUE".to_string()),
        "schedule_interval_seconds": row.get::<_, Option<i64>>("schedule_interval_seconds")?,
        "schedule_cron": row.get::<_, Option<String>>("schedule_cron")?,
        "schedule_rrule": row.get::<_, Option<String>>("schedule_rrule")?,
        "schedule_next_run_at": row.get::<_, Option<String>>("schedule_next_run_at")?,
        "schedule_enabled": row.get::<_, i64>("schedule_enabled")? != 0,
        "work_pool_id": row
            .get::<_, Option<String>>("work_pool_id")?
            .unwrap_or_else(|| DEFAULT_WORK_POOL_ID.to_string()),
        "created_at": row.get::<_, String>("created_at")?,
        "updated_at": row.get::<_, String>("updated_at")?,
    }))
}

pub(crate) fn deployment_run_row_to_json(row: &rusqlite::Row) -> SqlResult<Value> {
    let requested: Value =
        serde_json::from_str(row.get::<_, String>("requested_parameters")?.as_str())
            .unwrap_or(json!({}));
    let resolved: Value =
        serde_json::from_str(row.get::<_, String>("resolved_parameters")?.as_str())
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
        "parent_flow_run_id": row.get::<_, Option<String>>("parent_flow_run_id")?,
        "parent_task_run_id": row.get::<_, Option<String>>("parent_task_run_id")?,
        "parent_deployment_run_id": row.get::<_, Option<String>>("parent_deployment_run_id")?,
        "created_at": row.get::<_, String>("created_at")?,
        "updated_at": row.get::<_, String>("updated_at")?,
        "started_at": row.get::<_, Option<String>>("started_at")?,
        "finished_at": row.get::<_, Option<String>>("finished_at")?,
    }))
}
