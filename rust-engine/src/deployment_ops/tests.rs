use super::*;
use chrono::{Duration, Utc};
use rusqlite::{params, Connection};
use serde_json::json;

#[test]
fn create_deployment_sets_work_pool_id() {
    let conn = Connection::open_in_memory().expect("open db");
    conn.execute_batch(
        "CREATE TABLE deployments (
                id TEXT UNIQUE NOT NULL,
                name TEXT UNIQUE NOT NULL,
                flow_name TEXT NOT NULL,
                entrypoint TEXT,
                path TEXT,
                default_parameters TEXT NOT NULL,
                paused INTEGER NOT NULL,
                concurrency_limit INTEGER,
                collision_strategy TEXT NOT NULL DEFAULT 'ENQUEUE',
                schedule_interval_seconds INTEGER,
                schedule_cron TEXT,
                schedule_rrule TEXT,
                schedule_next_run_at TEXT,
                schedule_enabled INTEGER NOT NULL DEFAULT 0,
                work_pool_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );",
    )
    .expect("schema");

    let custom = create_deployment(
        &conn,
        &json!({
            "name": "pool-bound",
            "flow_name": "simple_flow",
            "work_pool_id": "custom-process-pool",
        }),
    )
    .expect("create with pool");
    assert_eq!(custom["work_pool_id"], "custom-process-pool");

    let default_pool = create_deployment(
        &conn,
        &json!({
            "name": "default-pool",
            "flow_name": "simple_flow",
        }),
    )
    .expect("create default pool");
    assert_eq!(default_pool["work_pool_id"], DEFAULT_WORK_POOL_ID);
}

#[test]
fn next_cron_occurrence_advances() {
    let t0 = Utc::now();
    let t1 = next_cron_occurrence("0 * * * * *", t0).expect("parse");
    assert!(t1 > t0);
}

#[test]
fn next_rrule_occurrence_advances() {
    let t0 = Utc::now();
    let t1 = next_rrule_occurrence("FREQ=MINUTELY;INTERVAL=5", t0).expect("parse");
    assert!(t1 > t0);
    assert_eq!(t1 - t0, Duration::minutes(5));
}

#[test]
fn next_rrule_rejects_count() {
    let err = next_rrule_occurrence("FREQ=DAILY;COUNT=3", Utc::now()).expect_err("COUNT rejected");
    assert!(err.contains("COUNT"));
}

#[test]
fn trigger_deployment_run_persists_parent_linkage() {
    let conn = Connection::open_in_memory().expect("open db");
    conn.execute_batch(
        "CREATE TABLE deployments (
                id TEXT UNIQUE NOT NULL,
                name TEXT UNIQUE NOT NULL,
                flow_name TEXT NOT NULL,
                default_parameters TEXT NOT NULL,
                paused INTEGER NOT NULL,
                concurrency_limit INTEGER,
                collision_strategy TEXT NOT NULL DEFAULT 'ENQUEUE'
            );
            CREATE TABLE deployment_runs (
                id TEXT UNIQUE NOT NULL,
                deployment_id TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_parameters TEXT NOT NULL,
                resolved_parameters TEXT NOT NULL,
                idempotency_key TEXT,
                worker_name TEXT,
                lease_until TEXT,
                flow_run_id TEXT,
                error TEXT,
                parent_flow_run_id TEXT,
                parent_task_run_id TEXT,
                parent_deployment_run_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );",
    )
    .expect("schema");
    conn.execute(
            "INSERT INTO deployments (id,name,flow_name,default_parameters,paused) VALUES (?1,?2,?3,?4,0)",
            params!["dep-1", "child", "child_flow", "{}"],
        )
        .expect("insert dep");

    let parent_link = DeploymentParentLink {
        parent_flow_run_id: Some("flow-parent".to_string()),
        parent_task_run_id: Some("task-parent".to_string()),
        parent_deployment_run_id: Some("dep-run-parent".to_string()),
    };
    let run = trigger_deployment_run(
        &conn,
        "dep-1",
        Some(&json!({"n": 1})),
        None,
        Some(&parent_link),
    )
    .expect("trigger");
    assert_eq!(run["parent_flow_run_id"], "flow-parent");
    assert_eq!(run["parent_task_run_id"], "task-parent");
    assert_eq!(run["parent_deployment_run_id"], "dep-run-parent");
}

#[test]
fn cancel_deployment_runs_for_parent_flow_cancels_scheduled_children() {
    let conn = Connection::open_in_memory().expect("open");
    conn.execute_batch(
        "CREATE TABLE deployment_runs (
                id TEXT UNIQUE NOT NULL,
                deployment_id TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_parameters TEXT NOT NULL,
                resolved_parameters TEXT NOT NULL,
                idempotency_key TEXT,
                worker_name TEXT,
                lease_until TEXT,
                flow_run_id TEXT,
                error TEXT,
                parent_flow_run_id TEXT,
                parent_task_run_id TEXT,
                parent_deployment_run_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );",
    )
    .expect("schema");
    conn.execute(
            "INSERT INTO deployment_runs (id,deployment_id,status,requested_parameters,resolved_parameters,created_at,updated_at,parent_flow_run_id,parent_task_run_id) \
             VALUES ('dr-1','dep-1','SCHEDULED','{}','{}','2020-01-01T00:00:00Z','2020-01-01T00:00:00Z','parent-flow','task-1')",
            [],
        )
        .expect("insert");

    let cancelled = cancel_deployment_runs_for_parent_flow(&conn, "parent-flow").expect("cancel");
    assert_eq!(cancelled.len(), 1);
    assert_eq!(cancelled[0]["id"], "dr-1");
    assert_eq!(cancelled[0]["status"], "CANCELLED");

    let status: String = conn
        .query_row(
            "SELECT status FROM deployment_runs WHERE id = 'dr-1'",
            [],
            |row| row.get(0),
        )
        .expect("status");
    assert_eq!(status, "CANCELLED");
}
