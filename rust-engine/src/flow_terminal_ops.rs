//! Resolve a flow-run terminal state from contributing child task-run states.
//!
//! Hot path: single SQLite scan + deterministic priority fold. Detached tasks
//! (`contribute_to_flow_state = 0`) are excluded. Missing column / NULL treated
//! as contributing (default true).

use rusqlite::{params, Connection};
use serde_json::{json, Value};

const TERMINAL_COMPLETED: &str = "COMPLETED";
const TERMINAL_FAILED: &str = "FAILED";
const TERMINAL_CANCELLED: &str = "CANCELLED";

/// Child row used by the pure fold (also convenient for unit tests).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChildState {
    pub id: String,
    pub task_name: String,
    pub state: String,
    pub kind: String,
    pub child_deployment_run_id: Option<String>,
}

/// Pure fold over contributing child states.
///
/// Priority: CANCELLED > FAILED > non-terminal → FAILED(incomplete) > COMPLETED.
pub fn fold_terminal_state(children: &[ChildState]) -> Value {
    let mut counts = json!({
        "total": children.len(),
        "COMPLETED": 0,
        "FAILED": 0,
        "CANCELLED": 0,
        "non_terminal": 0,
        "other": 0,
    });

    let mut sample_failures: Vec<Value> = Vec::new();
    let mut sample_cancelled: Vec<Value> = Vec::new();
    let mut sample_incomplete: Vec<Value> = Vec::new();

    for child in children {
        let st = child.state.as_str();
        match st {
            "COMPLETED" => {
                bump(&mut counts, "COMPLETED");
            }
            "FAILED" => {
                bump(&mut counts, "FAILED");
                if sample_failures.len() < 8 {
                    sample_failures.push(child_sample(child));
                }
            }
            "CANCELLED" => {
                bump(&mut counts, "CANCELLED");
                if sample_cancelled.len() < 8 {
                    sample_cancelled.push(child_sample(child));
                }
            }
            "SCHEDULED" | "PENDING" | "RUNNING" | "PAUSED" | "CANCELLING" => {
                bump(&mut counts, "non_terminal");
                if sample_incomplete.len() < 8 {
                    sample_incomplete.push(child_sample(child));
                }
            }
            _ => {
                bump(&mut counts, "other");
                // Unknown state: treat as failure for predictability.
                bump(&mut counts, "FAILED");
                if sample_failures.len() < 8 {
                    sample_failures.push(child_sample(child));
                }
            }
        }
    }

    if children.is_empty() {
        return json!({
            "ok": true,
            "state": TERMINAL_COMPLETED,
            "kind": "empty",
            "counts": counts,
            "sample_failures": [],
            "sample_cancelled": [],
            "sample_incomplete": [],
        });
    }

    if counts["CANCELLED"].as_u64().unwrap_or(0) > 0 {
        return json!({
            "ok": true,
            "state": TERMINAL_CANCELLED,
            "kind": "child_cancelled",
            "counts": counts,
            "sample_failures": sample_failures,
            "sample_cancelled": sample_cancelled,
            "sample_incomplete": sample_incomplete,
        });
    }
    if counts["FAILED"].as_u64().unwrap_or(0) > 0 {
        return json!({
            "ok": true,
            "state": TERMINAL_FAILED,
            "kind": "child_failed",
            "counts": counts,
            "sample_failures": sample_failures,
            "sample_cancelled": sample_cancelled,
            "sample_incomplete": sample_incomplete,
        });
    }
    if counts["non_terminal"].as_u64().unwrap_or(0) > 0 {
        return json!({
            "ok": true,
            "state": TERMINAL_FAILED,
            "kind": "incomplete_children",
            "counts": counts,
            "sample_failures": sample_failures,
            "sample_cancelled": sample_cancelled,
            "sample_incomplete": sample_incomplete,
        });
    }

    json!({
        "ok": true,
        "state": TERMINAL_COMPLETED,
        "kind": "all_completed",
        "counts": counts,
        "sample_failures": sample_failures,
        "sample_cancelled": sample_cancelled,
        "sample_incomplete": sample_incomplete,
    })
}

fn bump(counts: &mut Value, key: &str) {
    let n = counts[key].as_u64().unwrap_or(0) + 1;
    counts[key] = json!(n);
}

fn child_sample(child: &ChildState) -> Value {
    json!({
        "id": child.id,
        "task_name": child.task_name,
        "state": child.state,
        "kind": child.kind,
        "child_deployment_run_id": child.child_deployment_run_id,
    })
}

fn ensure_contribute_column(conn: &Connection) -> Result<(), String> {
    let mut stmt = conn
        .prepare("PRAGMA table_info(task_runs)")
        .map_err(|e| e.to_string())?;
    let cols: Vec<String> = stmt
        .query_map([], |row| row.get::<_, String>(1))
        .map_err(|e| e.to_string())?
        .filter_map(|r| r.ok())
        .collect();
    if !cols.iter().any(|c| c == "contribute_to_flow_state") {
        conn.execute(
            "ALTER TABLE task_runs ADD COLUMN contribute_to_flow_state INTEGER NOT NULL DEFAULT 1",
            [],
        )
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Load contributing child rows for ``flow_run_id`` (excludes detached).
pub fn load_contributing_children(
    conn: &Connection,
    flow_run_id: &str,
) -> Result<Vec<ChildState>, String> {
    // Column may be absent on older DBs; ensure then filter.
    let _ = ensure_contribute_column(conn);

    let mut stmt = conn
        .prepare(
            "SELECT id, task_name, state, COALESCE(kind, 'task'), child_deployment_run_id \
             FROM task_runs \
             WHERE flow_run_id = ?1 \
               AND COALESCE(contribute_to_flow_state, 1) != 0 \
             ORDER BY seq ASC",
        )
        .map_err(|e| e.to_string())?;

    let rows = stmt
        .query_map(params![flow_run_id], |row| {
            Ok(ChildState {
                id: row.get(0)?,
                task_name: row.get(1)?,
                state: row.get(2)?,
                kind: row.get(3)?,
                child_deployment_run_id: row.get(4)?,
            })
        })
        .map_err(|e| e.to_string())?;

    let mut out = Vec::new();
    for row in rows {
        out.push(row.map_err(|e| e.to_string())?);
    }
    Ok(out)
}

/// List contributing children (open + terminal) for wait/orchestration in Python.
pub fn list_contributing_children(conn: &Connection, body: &Value) -> Result<Value, String> {
    let flow_run_id = body
        .get("flow_run_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "missing flow_run_id".to_string())?;
    let children = load_contributing_children(conn, flow_run_id)?;
    let items: Vec<Value> = children
        .into_iter()
        .map(|c| {
            json!({
                "id": c.id,
                "task_name": c.task_name,
                "state": c.state,
                "kind": c.kind,
                "child_deployment_run_id": c.child_deployment_run_id,
            })
        })
        .collect();
    Ok(json!({"ok": true, "items": items}))
}

/// Resolve terminal flow state from contributing task_runs rows.
pub fn resolve_flow_terminal_state(conn: &Connection, body: &Value) -> Result<Value, String> {
    let flow_run_id = body
        .get("flow_run_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "missing flow_run_id".to_string())?;
    let children = load_contributing_children(conn, flow_run_id)?;
    Ok(fold_terminal_state(&children))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn child(id: &str, name: &str, state: &str, kind: &str) -> ChildState {
        ChildState {
            id: id.to_string(),
            task_name: name.to_string(),
            state: state.to_string(),
            kind: kind.to_string(),
            child_deployment_run_id: None,
        }
    }

    #[test]
    fn empty_children_completes() {
        let out = fold_terminal_state(&[]);
        assert_eq!(out["state"], "COMPLETED");
        assert_eq!(out["kind"], "empty");
    }

    #[test]
    fn all_completed() {
        let out = fold_terminal_state(&[
            child("1", "a", "COMPLETED", "task"),
            child("2", "b", "COMPLETED", "gate"),
        ]);
        assert_eq!(out["state"], "COMPLETED");
        assert_eq!(out["kind"], "all_completed");
    }

    #[test]
    fn cancelled_beats_failed() {
        let out = fold_terminal_state(&[
            child("1", "a", "FAILED", "task"),
            child("2", "b", "CANCELLED", "task"),
        ]);
        assert_eq!(out["state"], "CANCELLED");
        assert_eq!(out["kind"], "child_cancelled");
    }

    #[test]
    fn failed_beats_completed() {
        let out = fold_terminal_state(&[
            child("1", "a", "COMPLETED", "task"),
            child("2", "b", "FAILED", "subflow"),
        ]);
        assert_eq!(out["state"], "FAILED");
        assert_eq!(out["kind"], "child_failed");
        assert_eq!(out["sample_failures"][0]["task_name"], "b");
    }

    #[test]
    fn incomplete_after_barrier_is_failed() {
        let out = fold_terminal_state(&[
            child("1", "a", "COMPLETED", "task"),
            child("2", "b", "RUNNING", "task"),
        ]);
        assert_eq!(out["state"], "FAILED");
        assert_eq!(out["kind"], "incomplete_children");
    }

    fn open_task_db() -> Connection {
        let conn = Connection::open_in_memory().expect("db");
        conn.execute_batch(
            "CREATE TABLE task_runs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                flow_run_id TEXT NOT NULL,
                task_name TEXT NOT NULL,
                planned_node_id TEXT,
                state TEXT NOT NULL,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'task',
                child_flow_run_id TEXT,
                child_deployment_run_id TEXT,
                contribute_to_flow_state INTEGER NOT NULL DEFAULT 1
            );",
        )
        .expect("schema");
        conn
    }

    fn insert(
        conn: &Connection,
        id: &str,
        flow: &str,
        name: &str,
        state: &str,
        kind: &str,
        contribute: i64,
    ) {
        conn.execute(
            "INSERT INTO task_runs(id,flow_run_id,task_name,state,version,created_at,updated_at,kind,contribute_to_flow_state)
             VALUES(?,?,?,?,0,'t','t',?,?)",
            params![id, flow, name, state, kind, contribute],
        )
        .unwrap();
    }

    #[test]
    fn resolve_excludes_detached_and_aggregates() {
        let conn = open_task_db();
        insert(&conn, "t1", "f1", "ok", "COMPLETED", "task", 1);
        insert(&conn, "t2", "f1", "boom", "FAILED", "task", 0); // detached
        insert(&conn, "t3", "f1", "gate", "COMPLETED", "gate", 1);
        let out = resolve_flow_terminal_state(&conn, &json!({"flow_run_id": "f1"})).unwrap();
        assert_eq!(out["state"], "COMPLETED");
        assert_eq!(out["counts"]["total"], 2);
    }

    #[test]
    fn resolve_sees_failed_contributing() {
        let conn = open_task_db();
        insert(&conn, "t1", "f1", "ok", "COMPLETED", "task", 1);
        insert(&conn, "t2", "f1", "boom", "FAILED", "task", 1);
        let out = resolve_flow_terminal_state(&conn, &json!({"flow_run_id": "f1"})).unwrap();
        assert_eq!(out["state"], "FAILED");
        assert_eq!(out["kind"], "child_failed");
    }

    #[test]
    fn list_contributing_children_filters_detached() {
        let conn = open_task_db();
        insert(&conn, "t1", "f1", "a", "PENDING", "subflow", 1);
        insert(&conn, "t2", "f1", "b", "PENDING", "task", 0);
        let out = list_contributing_children(&conn, &json!({"flow_run_id": "f1"})).unwrap();
        let items = out["items"].as_array().unwrap();
        assert_eq!(items.len(), 1);
        assert_eq!(items[0]["id"], "t1");
    }

    #[test]
    fn resolve_adds_column_when_missing() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE task_runs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                flow_run_id TEXT NOT NULL,
                task_name TEXT NOT NULL,
                state TEXT NOT NULL,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'task',
                child_deployment_run_id TEXT
            );",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO task_runs(id,flow_run_id,task_name,state,version,created_at,updated_at)
             VALUES('t1','f1','a','COMPLETED',0,'t','t')",
            [],
        )
        .unwrap();
        let out = resolve_flow_terminal_state(&conn, &json!({"flow_run_id": "f1"})).unwrap();
        assert_eq!(out["state"], "COMPLETED");
    }
}
