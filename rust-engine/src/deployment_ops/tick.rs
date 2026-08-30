use chrono::{Duration, Utc};
use rusqlite::{params, Connection};
use serde_json::json;

use super::claim::trigger_deployment_run_tx;
use super::rows::now_iso;
use super::schedule::{next_cron_occurrence, next_rrule_occurrence};

/// Fire due interval schedules: enqueue runs and advance `schedule_next_run_at`.
fn tick_interval_schedules(conn: &Connection) -> Result<u64, String> {
    let now = now_iso();
    let mut stmt = conn
        .prepare(
            "SELECT id, schedule_interval_seconds, schedule_next_run_at \
             FROM deployments \
             WHERE schedule_enabled = 1 AND paused = 0 \
             AND schedule_interval_seconds IS NOT NULL AND schedule_interval_seconds > 0 \
             AND schedule_next_run_at IS NOT NULL AND schedule_next_run_at <= ?1",
        )
        .map_err(|e| e.to_string())?;
    let ids: Vec<(String, i64)> = stmt
        .query_map(params![now], |row| Ok((row.get(0)?, row.get(1)?)))
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    drop(stmt);

    let mut fired: u64 = 0;
    for (dep_id, interval_sec) in ids {
        let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
        if let Err(e) = trigger_deployment_run_tx(&tx, &dep_id, Some(&json!({})), None, None) {
            tx.rollback().map_err(|e| e.to_string())?;
            if e == "deployment not found" {
                continue;
            }
            return Err(e);
        }
        let next = (Utc::now() + Duration::seconds(interval_sec)).to_rfc3339();
        let ts = now_iso();
        tx.execute(
            "UPDATE deployments SET schedule_next_run_at = ?1, updated_at = ?2 WHERE id = ?3",
            params![next, ts, dep_id],
        )
        .map_err(|e| e.to_string())?;
        tx.commit().map_err(|e| e.to_string())?;
        fired += 1;
    }
    Ok(fired)
}

/// Cron-based schedules (mutually exclusive with interval/RRule in application logic).
fn tick_cron_schedules(conn: &Connection) -> Result<u64, String> {
    let now = now_iso();
    let mut stmt = conn
        .prepare(
            "SELECT id, schedule_cron, schedule_next_run_at \
             FROM deployments \
             WHERE schedule_enabled = 1 AND paused = 0 \
             AND schedule_cron IS NOT NULL AND trim(schedule_cron) != '' \
             AND (schedule_rrule IS NULL OR trim(schedule_rrule) = '') \
             AND (schedule_interval_seconds IS NULL OR schedule_interval_seconds <= 0) \
             AND schedule_next_run_at IS NOT NULL AND schedule_next_run_at <= ?1",
        )
        .map_err(|e| e.to_string())?;
    let ids: Vec<(String, String)> = stmt
        .query_map(params![now], |row| Ok((row.get(0)?, row.get(1)?)))
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    drop(stmt);

    let mut fired: u64 = 0;
    for (dep_id, cron_expr) in ids {
        let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
        if let Err(e) = trigger_deployment_run_tx(&tx, &dep_id, Some(&json!({})), None, None) {
            tx.rollback().map_err(|e| e.to_string())?;
            if e == "deployment not found" {
                continue;
            }
            return Err(e);
        }
        let after = Utc::now();
        let next = next_cron_occurrence(&cron_expr, after)?.to_rfc3339();
        let ts = now_iso();
        tx.execute(
            "UPDATE deployments SET schedule_next_run_at = ?1, updated_at = ?2 WHERE id = ?3",
            params![next, ts, dep_id],
        )
        .map_err(|e| e.to_string())?;
        tx.commit().map_err(|e| e.to_string())?;
        fired += 1;
    }
    Ok(fired)
}

/// RRule-based schedules (small deterministic subset).
fn tick_rrule_schedules(conn: &Connection) -> Result<u64, String> {
    let now = now_iso();
    let mut stmt = conn
        .prepare(
            "SELECT id, schedule_rrule, schedule_next_run_at \
             FROM deployments \
             WHERE schedule_enabled = 1 AND paused = 0 \
             AND schedule_rrule IS NOT NULL AND trim(schedule_rrule) != '' \
             AND schedule_next_run_at IS NOT NULL AND schedule_next_run_at <= ?1",
        )
        .map_err(|e| e.to_string())?;
    let ids: Vec<(String, String)> = stmt
        .query_map(params![now], |row| Ok((row.get(0)?, row.get(1)?)))
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    drop(stmt);

    let mut fired: u64 = 0;
    for (dep_id, rrule_expr) in ids {
        let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
        if let Err(e) = trigger_deployment_run_tx(&tx, &dep_id, Some(&json!({})), None, None) {
            tx.rollback().map_err(|e| e.to_string())?;
            if e == "deployment not found" {
                continue;
            }
            return Err(e);
        }
        let next = next_rrule_occurrence(&rrule_expr, Utc::now())?.to_rfc3339();
        let ts = now_iso();
        tx.execute(
            "UPDATE deployments SET schedule_next_run_at = ?1, updated_at = ?2 WHERE id = ?3",
            params![next, ts, dep_id],
        )
        .map_err(|e| e.to_string())?;
        tx.commit().map_err(|e| e.to_string())?;
        fired += 1;
    }
    Ok(fired)
}

/// Fire due interval, cron, and RRule schedules.
pub fn tick_deployment_schedules(conn: &Connection) -> Result<u64, String> {
    let a = tick_interval_schedules(conn)?;
    let b = tick_cron_schedules(conn)?;
    let c = tick_rrule_schedules(conn)?;
    Ok(a + b + c)
}
