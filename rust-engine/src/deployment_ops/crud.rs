use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};
use uuid::Uuid;

use super::rows::{deployment_row_to_json, now_iso, DEFAULT_WORK_POOL_ID};
use super::schedule::ScheduleFields;

const DEPLOYMENT_SELECT: &str = "SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,\
         concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,\
         schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at \
         FROM deployments";

fn opt_json_str(v: &Value) -> Option<String> {
    if v.is_null() {
        None
    } else {
        v.as_str().map(|s| s.to_string())
    }
}

fn patch_opt_str(slot: &mut Option<String>, body: &Value, key: &str) {
    if let Some(v) = body.get(key) {
        *slot = opt_json_str(v);
    }
}

pub fn create_deployment(conn: &Connection, body: &Value) -> Result<Value, String> {
    let name = body
        .get("name")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "missing string field name".to_string())?;
    let flow_name = body
        .get("flow_name")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "missing string field flow_name".to_string())?;
    let existing: Option<Value> = conn
        .query_row(
            &format!(
                "{DEPLOYMENT_SELECT} WHERE name = ?1{} LIMIT 1",
                crate::flow_catalog_ops::deployments_not_deleted_sql(conn)
            ),
            params![name],
            deployment_row_to_json,
        )
        .optional()
        .map_err(|e| e.to_string())?;
    if let Some(row) = existing {
        return Ok(row);
    }

    let mut schedule = ScheduleFields::from_create_body(body);
    schedule.normalize_exclusive();
    schedule.fill_next_run()?;

    let deployment_id = Uuid::new_v4().to_string();
    let now = now_iso();
    let flow_id = body.get("flow_id").and_then(|v| v.as_str());
    let has_flow_id = crate::flow_catalog_ops::column_exists(conn, "deployments", "flow_id");
    if has_flow_id {
        conn.execute(
            "INSERT INTO deployments \
             (id,name,flow_name,entrypoint,path,default_parameters,paused,\
              concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,\
              schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at,flow_id) \
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18)",
            params![
                deployment_id,
                name,
                flow_name,
                body.get("entrypoint").and_then(|v| v.as_str()),
                body.get("path").and_then(|v| v.as_str()),
                serde_json::to_string(body.get("default_parameters").unwrap_or(&json!({})))
                    .map_err(|e| e.to_string())?,
                body.get("paused").and_then(|v| v.as_bool()).unwrap_or(false) as i64,
                body.get("concurrency_limit")
                    .and_then(|v| v.as_i64().or_else(|| v.as_u64().map(|u| u as i64))),
                body.get("collision_strategy")
                    .and_then(|v| v.as_str())
                    .unwrap_or("ENQUEUE"),
                schedule.interval,
                schedule.cron,
                schedule.rrule,
                schedule.next_run_at,
                i64::from(schedule.enabled),
                body.get("work_pool_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or(DEFAULT_WORK_POOL_ID),
                now,
                now,
                flow_id,
            ],
        )
        .map_err(|e| e.to_string())?;
    } else {
        conn.execute(
            "INSERT INTO deployments \
             (id,name,flow_name,entrypoint,path,default_parameters,paused,\
              concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,\
              schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at) \
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17)",
            params![
                deployment_id,
                name,
                flow_name,
                body.get("entrypoint").and_then(|v| v.as_str()),
                body.get("path").and_then(|v| v.as_str()),
                serde_json::to_string(body.get("default_parameters").unwrap_or(&json!({})))
                    .map_err(|e| e.to_string())?,
                body.get("paused").and_then(|v| v.as_bool()).unwrap_or(false) as i64,
                body.get("concurrency_limit")
                    .and_then(|v| v.as_i64().or_else(|| v.as_u64().map(|u| u as i64))),
                body.get("collision_strategy")
                    .and_then(|v| v.as_str())
                    .unwrap_or("ENQUEUE"),
                schedule.interval,
                schedule.cron,
                schedule.rrule,
                schedule.next_run_at,
                i64::from(schedule.enabled),
                body.get("work_pool_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or(DEFAULT_WORK_POOL_ID),
                now,
                now,
            ],
        )
        .map_err(|e| e.to_string())?;
    }

    conn.query_row(
        &format!("{DEPLOYMENT_SELECT} WHERE id = ?1"),
        params![deployment_id],
        deployment_row_to_json,
    )
    .map_err(|e| e.to_string())
}

/// Partial update of a deployment row (`null` JSON fields mean leave unchanged).
pub fn update_deployment(conn: &Connection, body: &Value) -> Result<Value, String> {
    let deployment_id = body
        .get("deployment_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "missing string field deployment_id".to_string())?;
    let row = conn
        .query_row(
            &format!("{DEPLOYMENT_SELECT} WHERE id = ?1 LIMIT 1"),
            params![deployment_id],
            deployment_row_to_json,
        )
        .optional()
        .map_err(|e| e.to_string())?;
    let Some(current) = row else {
        return Err("deployment not found".to_string());
    };

    let mut entrypoint = current.get("entrypoint").map(opt_json_str).unwrap_or(None);
    let mut path = current.get("path").map(opt_json_str).unwrap_or(None);
    patch_opt_str(&mut entrypoint, body, "entrypoint");
    patch_opt_str(&mut path, body, "path");

    let mut default_parameters = current
        .get("default_parameters")
        .cloned()
        .unwrap_or(json!({}));
    if let Some(v) = body.get("default_parameters") {
        if !v.is_null() {
            default_parameters = v.clone();
        }
    }
    let mut paused = current
        .get("paused")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if let Some(v) = body.get("paused") {
        if !v.is_null() {
            paused = v.as_bool().unwrap_or(false);
        }
    }
    let mut concurrency_limit = current
        .get("concurrency_limit")
        .and_then(|v| v.as_i64().or_else(|| v.as_u64().map(|u| u as i64)));
    if let Some(v) = body.get("concurrency_limit") {
        concurrency_limit = if v.is_null() {
            None
        } else {
            v.as_i64().or_else(|| v.as_u64().map(|u| u as i64))
        };
    }
    let mut collision_strategy = current
        .get("collision_strategy")
        .and_then(|v| v.as_str())
        .unwrap_or("ENQUEUE")
        .to_string();
    if let Some(v) = body.get("collision_strategy") {
        if !v.is_null() {
            collision_strategy = v.as_str().unwrap_or("ENQUEUE").to_string();
        }
    }

    let mut schedule = ScheduleFields::from_row(&current);
    schedule.apply_body(body);
    schedule.normalize_exclusive();
    schedule.fill_next_run()?;

    conn.execute(
        "UPDATE deployments SET \
         entrypoint = ?1, path = ?2, default_parameters = ?3, paused = ?4, \
         concurrency_limit = ?5, collision_strategy = ?6, \
         schedule_interval_seconds = ?7, schedule_cron = ?8, schedule_rrule = ?9, schedule_next_run_at = ?10, \
         schedule_enabled = ?11, updated_at = ?12 \
         WHERE id = ?13",
        params![
            entrypoint,
            path,
            serde_json::to_string(&default_parameters).map_err(|e| e.to_string())?,
            i64::from(paused),
            concurrency_limit,
            collision_strategy,
            schedule.interval,
            schedule.cron,
            schedule.rrule,
            schedule.next_run_at,
            i64::from(schedule.enabled),
            now_iso(),
            deployment_id,
        ],
    )
    .map_err(|e| e.to_string())?;

    conn.query_row(
        &format!("{DEPLOYMENT_SELECT} WHERE id = ?1"),
        params![deployment_id],
        deployment_row_to_json,
    )
    .map_err(|e| e.to_string())
}
