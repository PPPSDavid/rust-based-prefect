//! SQLite read-model projection helpers (subset spike: `task_runs` row updates).
//!
//! Mirrors `python-shim` `InMemoryControlPlane::_update_task_row` / `ui_write::persist_task_transition_with_conn`.

use rusqlite::{params, Connection};

/// Updates a single `task_runs` row — same SQL as the Python projection write path.
pub fn update_task_run_row(
    db_path: &str,
    task_run_id: &str,
    state: &str,
    version: i64,
    updated_at: &str,
) -> Result<(), String> {
    let conn = Connection::open(db_path).map_err(|e| e.to_string())?;
    conn.execute(
        "UPDATE task_runs SET state = ?, version = ?, updated_at = ? WHERE id = ?",
        params![state, version, updated_at, task_run_id],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;
    use std::path::PathBuf;
    use uuid::Uuid;

    fn temp_db() -> PathBuf {
        let mut p = std::env::temp_dir();
        p.push(format!("ironflow_projection_test_{}.db", Uuid::new_v4()));
        p
    }

    fn init_schema(conn: &Connection) {
        conn
            .execute_batch(
                r"
            CREATE TABLE task_runs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                flow_run_id TEXT NOT NULL,
                task_name TEXT NOT NULL,
                planned_node_id TEXT,
                state TEXT NOT NULL,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            ",
            )
            .unwrap();
    }

    #[test]
    fn update_task_run_row_matches_expected() {
        let path = temp_db();
        let db_path = path.to_str().unwrap();
        {
            let conn = Connection::open(db_path).unwrap();
            init_schema(&conn);
            conn.execute(
                "INSERT INTO task_runs(id,flow_run_id,task_name,planned_node_id,state,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                params![
                    "task-1",
                    "flow-1",
                    "t",
                    Option::<String>::None,
                    "PENDING",
                    1_i64,
                    "2020-01-01T00:00:00Z",
                    "2020-01-01T00:00:00Z"
                ],
            )
            .unwrap();
        }
        update_task_run_row(db_path, "task-1", "RUNNING", 2, "2026-05-11T12:00:00Z").unwrap();
        let conn = Connection::open(db_path).unwrap();
        let state: String = conn
            .query_row(
                "SELECT state FROM task_runs WHERE id = ?",
                params!["task-1"],
                |r| r.get(0),
            )
            .unwrap();
        let ver: i64 = conn
            .query_row(
                "SELECT version FROM task_runs WHERE id = ?",
                params!["task-1"],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(state, "RUNNING");
        assert_eq!(ver, 2);
        let _ = std::fs::remove_file(&path);
    }
}
