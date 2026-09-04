//! Flow-catalog hot paths: persist attach, list/query joins, set-based retention.
//! Operator rename/archive/delete stays in Python; this module owns scan/write volume.

use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};
use uuid::Uuid;

const LIVE_FLOW_STATES: &str = "'SCHEDULED','PENDING','RUNNING','PAUSED'";

pub(crate) fn table_exists(conn: &Connection, name: &str) -> bool {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1 LIMIT 1",
        params![name],
        |_| Ok(()),
    )
    .optional()
    .ok()
    .flatten()
    .is_some()
}

pub(crate) fn column_exists(conn: &Connection, table: &str, column: &str) -> bool {
    let Ok(mut stmt) = conn.prepare(&format!("PRAGMA table_info({table})")) else {
        return false;
    };
    let Ok(iter) = stmt.query_map([], |row| row.get::<_, String>(1)) else {
        return false;
    };
    let names: Vec<String> = iter.filter_map(Result::ok).collect();
    names.iter().any(|name| name == column)
}

/// Skip soft-deleted deployments when the column exists (legacy test schemas omit it).
pub(crate) fn deployments_not_deleted_sql(conn: &Connection) -> &'static str {
    if column_exists(conn, "deployments", "deleted_at") {
        " AND deleted_at IS NULL"
    } else {
        ""
    }
}

/// Upsert a `flows` row for `flow_name` and set `flow_runs.flow_id`.
/// No-op when the catalog tables/columns are missing (in-memory engine tests).
pub fn attach_flow_run_to_catalog(
    conn: &Connection,
    run_id: &str,
    flow_name: &str,
) -> Result<(), String> {
    if !table_exists(conn, "flows") || !column_exists(conn, "flow_runs", "flow_id") {
        return Ok(());
    }
    if lookup_flow_id(conn, flow_name)?.is_none() && alias_owner(conn, flow_name)?.is_some() {
        // Do not fork a second identity when the persist name is only a reserved alias.
        return Ok(());
    }
    let flow_id = resolve_or_insert_flow_id(conn, flow_name)?;
    conn.execute(
        "UPDATE flow_runs SET flow_id = ?1 WHERE id = ?2",
        params![flow_id, run_id],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

fn resolve_or_insert_flow_id(conn: &Connection, flow_name: &str) -> Result<String, String> {
    if let Some(id) = lookup_flow_id(conn, flow_name)? {
        return Ok(id);
    }
    let now = Utc::now().to_rfc3339();
    let new_id = Uuid::new_v4().to_string();
    conn.execute(
        "INSERT OR IGNORE INTO flows(id,name,status,created_at,updated_at) VALUES(?1,?2,?3,?4,?5)",
        params![new_id, flow_name, "active", now, now],
    )
    .map_err(|e| e.to_string())?;
    lookup_flow_id(conn, flow_name)?
        .ok_or_else(|| format!("failed to upsert catalog row for {flow_name}"))
}

fn lookup_flow_id(conn: &Connection, flow_name: &str) -> Result<Option<String>, String> {
    // Canonical name only. Alias names are reserved by ensure_flow_canonical and must
    // not silently attach (or fork a second catalog row).
    conn.query_row(
        "SELECT id FROM flows WHERE name = ?1 LIMIT 1",
        params![flow_name],
        |row| row.get(0),
    )
    .optional()
    .map_err(|e| e.to_string())
}

fn alias_owner(conn: &Connection, flow_name: &str) -> Result<Option<String>, String> {
    if !table_exists(conn, "flow_aliases") {
        return Ok(None);
    }
    conn.query_row(
        "SELECT flow_id FROM flow_aliases WHERE name = ?1 LIMIT 1",
        params![flow_name],
        |row| row.get(0),
    )
    .optional()
    .map_err(|e| e.to_string())
}

fn flow_row_json(conn: &Connection, flow_id: &str) -> Result<Value, String> {
    conn.query_row(
        "SELECT id,name,status,created_at,updated_at,archived_at,deleted_at \
         FROM flows WHERE id = ?1 LIMIT 1",
        params![flow_id],
        |row| {
            Ok(json!({
                "id": row.get::<_, String>(0)?,
                "name": row.get::<_, String>(1)?,
                "status": row.get::<_, String>(2)?,
                "created_at": row.get::<_, String>(3)?,
                "updated_at": row.get::<_, String>(4)?,
                "archived_at": row.get::<_, Option<String>>(5)?,
                "deleted_at": row.get::<_, Option<String>>(6)?,
            }))
        },
    )
    .map_err(|e| e.to_string())
}

/// Canonical-name upsert used on every `create_flow_run` / `create_deployment`.
/// Alias-only names return `alias_reserved`; deleted rows return `deleted_flow`.
pub fn ensure_flow_canonical(conn: &Connection, name: &str) -> Result<Value, String> {
    let canonical = name.trim();
    if canonical.is_empty() {
        return Ok(json!({
            "ok": false,
            "error": {"code": "invalid_name", "message": "flow name is required"}
        }));
    }
    if !table_exists(conn, "flows") {
        return Ok(json!({"ok": false, "fallback": true}));
    }
    if let Some(owner) = alias_owner(conn, canonical)? {
        if lookup_flow_id(conn, canonical)?.as_deref() != Some(owner.as_str()) {
            return Ok(json!({
                "ok": false,
                "error": {
                    "code": "alias_reserved",
                    "message": format!("name {canonical:?} is reserved as an alias of another flow")
                }
            }));
        }
    }
    if let Some(flow_id) = lookup_flow_id(conn, canonical)? {
        let flow = flow_row_json(conn, &flow_id)?;
        if flow.get("status").and_then(Value::as_str) == Some("deleted") {
            return Ok(json!({
                "ok": false,
                "error": {
                    "code": "deleted_flow",
                    "message": format!("flow {canonical:?} is deleted; restore it before reuse")
                }
            }));
        }
        return Ok(json!({"ok": true, "flow": flow}));
    }
    let flow_id = resolve_or_insert_flow_id(conn, canonical)?;
    let flow = flow_row_json(conn, &flow_id)?;
    Ok(json!({"ok": true, "flow": flow}))
}

fn parse_limit(params_json: &str, default_limit: i64) -> i64 {
    let parsed: Value = serde_json::from_str(params_json).unwrap_or_else(|_| json!({}));
    parsed
        .get("limit")
        .and_then(Value::as_i64)
        .filter(|v| *v > 0)
        .unwrap_or(default_limit)
}

fn parse_opt_string(params_json: &str, key: &str) -> Option<String> {
    let parsed: Value = serde_json::from_str(params_json).unwrap_or_else(|_| json!({}));
    parsed
        .get(key)
        .and_then(Value::as_str)
        .map(|s| s.to_string())
        .filter(|s| !s.is_empty())
}

fn parse_bool(params_json: &str, key: &str) -> bool {
    let parsed: Value = serde_json::from_str(params_json).unwrap_or_else(|_| json!({}));
    match parsed.get(key) {
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_i64().unwrap_or(0) != 0,
        Some(Value::String(s)) => matches!(s.to_ascii_lowercase().as_str(), "1" | "true" | "yes"),
        _ => false,
    }
}

fn catalog_status_predicate(hide_archived: bool) -> &'static str {
    if hide_archived {
        "(catalog.id IS NULL OR catalog.status = 'active')"
    } else {
        "(catalog.id IS NULL OR catalog.status IN ('active','archived'))"
    }
}

pub fn query_flow_runs(conn: &Connection, params_json: &str) -> Result<String, String> {
    let state = parse_opt_string(params_json, "state");
    let cursor = parse_opt_string(params_json, "cursor").and_then(|v| v.parse::<i64>().ok());
    let limit = parse_limit(params_json, 50);
    let hide_archived = parse_bool(params_json, "hide_archived");
    let has_catalog = table_exists(conn, "flows") && column_exists(conn, "flow_runs", "flow_id");
    let has_flow_id = column_exists(conn, "flow_runs", "flow_id");

    let mut sql = String::from(
        "SELECT fr.seq,fr.id,fr.name,fr.state,fr.version,fr.created_at,fr.updated_at,\
         fr.parent_flow_run_id,fr.parent_task_run_id,fr.root_flow_run_id,\
         fr.execution_mode,fr.depth",
    );
    if has_flow_id {
        sql.push_str(",fr.flow_id");
    }
    sql.push_str(" FROM flow_runs fr");
    if has_catalog {
        sql.push_str(" LEFT JOIN flows catalog ON catalog.id = fr.flow_id");
    }
    sql.push_str(" WHERE (?1 IS NULL OR fr.state = ?1)");
    sql.push_str(" AND (?2 IS NULL OR fr.seq < ?2)");
    if has_catalog {
        sql.push_str(" AND ");
        sql.push_str(catalog_status_predicate(hide_archived));
    }
    sql.push_str(" ORDER BY fr.seq DESC LIMIT ?3");

    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
    let items = stmt
        .query_map(params![state.as_deref(), cursor, limit], |row| {
            let mut obj = json!({
                "id": row.get::<_, String>(1)?,
                "name": row.get::<_, String>(2)?,
                "state": row.get::<_, String>(3)?,
                "version": row.get::<_, i64>(4)?,
                "created_at": row.get::<_, String>(5)?,
                "updated_at": row.get::<_, String>(6)?,
                "parent_flow_run_id": row.get::<_, Option<String>>(7)?,
                "parent_task_run_id": row.get::<_, Option<String>>(8)?,
                "root_flow_run_id": row.get::<_, Option<String>>(9)?,
                "execution_mode": row.get::<_, Option<String>>(10)?,
                "depth": row.get::<_, i64>(11)?,
                "seq": row.get::<_, i64>(0)?
            });
            if has_flow_id {
                if let Ok(fid) = row.get::<_, Option<String>>(12) {
                    obj["flow_id"] = json!(fid);
                }
            }
            Ok(obj)
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    page_with_cursor(items, limit)
}

pub fn query_flows(conn: &Connection, params_json: &str) -> Result<String, String> {
    if table_exists(conn, "flows") {
        return query_catalog_flows(conn, params_json);
    }
    query_legacy_flow_names(conn, params_json)
}

fn query_catalog_flows(conn: &Connection, params_json: &str) -> Result<String, String> {
    let limit = parse_limit(params_json, 200);
    let cursor = parse_opt_string(params_json, "cursor");
    let status = parse_opt_string(params_json, "status");
    let hide_archived = parse_bool(params_json, "hide_archived");
    let mut sql = String::from(
        "SELECT f.id,f.name,f.status,f.created_at,f.updated_at,f.archived_at,f.deleted_at,\
         COUNT(fr.id) AS run_count \
         FROM flows f LEFT JOIN flow_runs fr ON fr.flow_id = f.id",
    );
    let mut where_parts: Vec<&str> = Vec::new();
    if status.is_some() {
        where_parts.push("f.status = ?1");
    } else if hide_archived {
        where_parts.push("f.status = 'active'");
    } else {
        where_parts.push("f.status IN ('active','archived')");
    }
    if cursor.is_some() {
        where_parts.push("f.updated_at < ?2");
    }
    sql.push_str(" WHERE ");
    sql.push_str(&where_parts.join(" AND "));
    sql.push_str(
        " GROUP BY f.id,f.name,f.status,f.created_at,f.updated_at,f.archived_at,f.deleted_at",
    );
    sql.push_str(" ORDER BY f.updated_at DESC LIMIT ?3");

    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
    let items = stmt
        .query_map(
            params![status.as_deref(), cursor.as_deref(), limit],
            |row| {
                Ok(json!({
                    "id": row.get::<_, String>(0)?,
                    "name": row.get::<_, String>(1)?,
                    "status": row.get::<_, String>(2)?,
                    "created_at": row.get::<_, String>(3)?,
                    "updated_at": row.get::<_, String>(4)?,
                    "archived_at": row.get::<_, Option<String>>(5)?,
                    "deleted_at": row.get::<_, Option<String>>(6)?,
                    "run_count": row.get::<_, i64>(7)?
                }))
            },
        )
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    let next_cursor = next_updated_at_cursor(&items, limit);
    serde_json::to_string(&json!({
        "items": items,
        "next_cursor": next_cursor,
        "catalog": true
    }))
    .map_err(|e| e.to_string())
}

fn query_legacy_flow_names(conn: &Connection, params_json: &str) -> Result<String, String> {
    let limit = parse_limit(params_json, 200);
    let mut stmt = conn
        .prepare(
            "SELECT name,MAX(updated_at) AS updated_at,COUNT(*) AS run_count \
             FROM flow_runs GROUP BY name ORDER BY updated_at DESC LIMIT ?1",
        )
        .map_err(|e| e.to_string())?;
    let items = stmt
        .query_map(params![limit], |row| {
            Ok(json!({
                "name": row.get::<_, String>(0)?,
                "updated_at": row.get::<_, String>(1)?,
                "run_count": row.get::<_, i64>(2)?
            }))
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    serde_json::to_string(&json!({"items": items, "next_cursor": null, "catalog": false}))
        .map_err(|e| e.to_string())
}

fn next_updated_at_cursor(items: &[Value], limit: i64) -> Option<String> {
    if items.len() as i64 != limit {
        return None;
    }
    items
        .last()
        .and_then(|it| it.get("updated_at"))
        .and_then(Value::as_str)
        .map(str::to_string)
}

fn page_with_cursor(mut items: Vec<Value>, limit: i64) -> Result<String, String> {
    let next_cursor = if items.len() as i64 == limit {
        items
            .last()
            .and_then(|it| it.get("seq"))
            .and_then(Value::as_i64)
            .map(|n| n.to_string())
    } else {
        None
    };
    for item in &mut items {
        if let Some(obj) = item.as_object_mut() {
            obj.remove("seq");
        }
    }
    serde_json::to_string(&json!({
        "items": items,
        "next_cursor": next_cursor
    }))
    .map_err(|e| e.to_string())
}

/// Set-based TTL of terminal flow runs plus orphan catalog GC.
pub fn catalog_retention_sweep(
    conn: &Connection,
    cutoff: Option<&str>,
    gc_orphans: bool,
) -> Result<Value, String> {
    let deleted_runs = if let Some(cutoff) = cutoff.filter(|s| !s.is_empty()) {
        sweep_expired_runs(conn, cutoff)?
    } else {
        0
    };
    let gc_flows = if gc_orphans {
        sweep_orphan_flows(conn)?
    } else {
        0
    };
    Ok(json!({"deleted_runs": deleted_runs, "gc_flows": gc_flows}))
}

fn sweep_expired_runs(conn: &Connection, cutoff: &str) -> Result<i64, String> {
    let filter = format!(
        "SELECT id FROM flow_runs WHERE updated_at < ?1 AND state NOT IN ({LIVE_FLOW_STATES})"
    );
    let children = [
        ("task_runs", "flow_run_id"),
        ("logs", "flow_run_id"),
        ("events", "run_id"),
        ("artifacts", "flow_run_id"),
        ("dag_manifests", "flow_run_id"),
    ];
    for (table, col) in children {
        if !table_exists(conn, table) {
            continue;
        }
        let sql = format!("DELETE FROM {table} WHERE {col} IN ({filter})");
        conn.execute(&sql, params![cutoff])
            .map_err(|e| e.to_string())?;
    }
    let n = conn
        .execute(
            &format!(
                "DELETE FROM flow_runs WHERE updated_at < ?1 AND state NOT IN ({LIVE_FLOW_STATES})"
            ),
            params![cutoff],
        )
        .map_err(|e| e.to_string())?;
    Ok(n as i64)
}

fn sweep_orphan_flows(conn: &Connection) -> Result<i64, String> {
    if !table_exists(conn, "flows") || !column_exists(conn, "flow_runs", "flow_id") {
        return Ok(0);
    }
    let dep_alive = if table_exists(conn, "deployments")
        && column_exists(conn, "deployments", "flow_id")
    {
        if column_exists(conn, "deployments", "deleted_at") {
            "NOT EXISTS (SELECT 1 FROM deployments d WHERE d.flow_id = flows.id AND d.deleted_at IS NULL)"
        } else {
            "NOT EXISTS (SELECT 1 FROM deployments d WHERE d.flow_id = flows.id)"
        }
    } else {
        "1=1"
    };
    let orphan = format!(
        "status IN ('archived','deleted') \
         AND {dep_alive} \
         AND NOT EXISTS (SELECT 1 FROM flow_runs fr WHERE fr.flow_id = flows.id)"
    );
    if table_exists(conn, "flow_aliases") {
        conn.execute(
            &format!(
                "DELETE FROM flow_aliases WHERE flow_id IN (SELECT id FROM flows WHERE {orphan})"
            ),
            [],
        )
        .map_err(|e| e.to_string())?;
    }
    let n = conn
        .execute(&format!("DELETE FROM flows WHERE {orphan}"), [])
        .map_err(|e| e.to_string())?;
    Ok(n as i64)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::Engine;
    use crate::ui_write::persist_flow_create_with_conn;

    fn memory_catalog() -> Connection {
        let conn = Connection::open_in_memory().expect("db");
        conn.execute_batch(
            "CREATE TABLE flow_runs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                state TEXT NOT NULL,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                parent_flow_run_id TEXT,
                parent_task_run_id TEXT,
                root_flow_run_id TEXT,
                execution_mode TEXT,
                depth INTEGER NOT NULL DEFAULT 0,
                flow_id TEXT
            );
            CREATE TABLE task_runs (
                id TEXT PRIMARY KEY,
                flow_run_id TEXT NOT NULL
            );
            CREATE TABLE logs (
                id TEXT PRIMARY KEY,
                flow_run_id TEXT NOT NULL
            );
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL
            );
            CREATE TABLE artifacts (
                id TEXT PRIMARY KEY,
                flow_run_id TEXT NOT NULL
            );
            CREATE TABLE dag_manifests (
                flow_run_id TEXT PRIMARY KEY
            );
            CREATE TABLE flows (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT,
                deleted_at TEXT
            );
            CREATE TABLE flow_aliases (
                name TEXT PRIMARY KEY,
                flow_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE deployments (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                flow_name TEXT NOT NULL,
                flow_id TEXT,
                deleted_at TEXT,
                paused INTEGER NOT NULL DEFAULT 0,
                schedule_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );",
        )
        .expect("schema");
        conn
    }

    #[test]
    fn persist_attaches_flow_id_when_catalog_exists() {
        let conn = memory_catalog();
        let mut engine = Engine::new();
        let run = engine.create_flow_run("alpha");
        persist_flow_create_with_conn(&conn, &run).expect("persist");
        let flow_id: String = conn
            .query_row(
                "SELECT flow_id FROM flow_runs WHERE id = ?1",
                params![run.id.to_string()],
                |r| r.get(0),
            )
            .expect("flow_id");
        assert!(!flow_id.is_empty());
        let name: String = conn
            .query_row(
                "SELECT name FROM flows WHERE id = ?1",
                params![flow_id],
                |r| r.get(0),
            )
            .expect("catalog");
        assert_eq!(name, "alpha");
    }

    #[test]
    fn persist_skips_attach_without_flows_table() {
        let conn = Connection::open_in_memory().expect("db");
        conn.execute_batch(
            "CREATE TABLE flow_runs (
                id TEXT PRIMARY KEY,
                name TEXT,
                state TEXT,
                version INTEGER,
                created_at TEXT,
                updated_at TEXT,
                parent_flow_run_id TEXT,
                parent_task_run_id TEXT,
                root_flow_run_id TEXT,
                execution_mode TEXT,
                depth INTEGER
            );",
        )
        .expect("schema");
        let mut engine = Engine::new();
        let run = engine.create_flow_run("demo");
        persist_flow_create_with_conn(&conn, &run).expect("persist");
        let n: i64 = conn
            .query_row("SELECT COUNT(*) FROM flow_runs", [], |r| r.get(0))
            .expect("count");
        assert_eq!(n, 1);
    }

    #[test]
    fn query_flow_runs_hides_archived_catalog() {
        let conn = memory_catalog();
        let now = "2026-01-01T00:00:00+00:00";
        conn.execute(
            "INSERT INTO flows(id,name,status,created_at,updated_at,archived_at) VALUES('f-arch','old','archived',?1,?1,?1)",
            params![now],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO flows(id,name,status,created_at,updated_at) VALUES('f-live','live','active',?1,?1)",
            params![now],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO flow_runs(id,name,state,version,created_at,updated_at,depth,flow_id) \
             VALUES('r1','old','COMPLETED',0,?1,?1,0,'f-arch')",
            params![now],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO flow_runs(id,name,state,version,created_at,updated_at,depth,flow_id) \
             VALUES('r2','live','COMPLETED',0,?1,?1,0,'f-live')",
            params![now],
        )
        .unwrap();
        let hidden: Value = serde_json::from_str(
            &query_flow_runs(&conn, r#"{"hide_archived":true,"limit":50}"#).unwrap(),
        )
        .unwrap();
        let ids: Vec<&str> = hidden["items"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids, vec!["r2"]);
        let shown: Value = serde_json::from_str(
            &query_flow_runs(&conn, r#"{"hide_archived":false,"limit":50}"#).unwrap(),
        )
        .unwrap();
        assert_eq!(shown["items"].as_array().unwrap().len(), 2);
    }

    #[test]
    fn query_flows_returns_catalog_rows() {
        let conn = memory_catalog();
        let now = "2026-01-01T00:00:00+00:00";
        conn.execute(
            "INSERT INTO flows(id,name,status,created_at,updated_at) VALUES('f1','alpha','active',?1,?1)",
            params![now],
        )
        .unwrap();
        let payload: Value = serde_json::from_str(
            &query_flows(&conn, r#"{"hide_archived":true,"limit":50}"#).unwrap(),
        )
        .unwrap();
        assert_eq!(payload["catalog"], true);
        assert_eq!(payload["items"][0]["id"], "f1");
        assert_eq!(payload["items"][0]["run_count"], 0);
    }

    #[test]
    fn retention_sweep_is_set_based_and_fences_live_runs() {
        let conn = memory_catalog();
        let old = "2000-01-01T00:00:00+00:00";
        let now = "2026-01-01T00:00:00+00:00";
        conn.execute(
            "INSERT INTO flows(id,name,status,created_at,updated_at,deleted_at) \
             VALUES('orphan','gone','deleted',?1,?1,?1)",
            params![now],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO flow_runs(id,name,state,version,created_at,updated_at,depth,flow_id) \
             VALUES('expired','x','COMPLETED',0,?1,?1,0,NULL)",
            params![old],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO flow_runs(id,name,state,version,created_at,updated_at,depth,flow_id) \
             VALUES('live','y','RUNNING',0,?1,?1,0,NULL)",
            params![old],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO task_runs(id,flow_run_id) VALUES('t1','expired')",
            [],
        )
        .unwrap();
        let summary =
            catalog_retention_sweep(&conn, Some("2010-01-01T00:00:00+00:00"), true).unwrap();
        assert_eq!(summary["deleted_runs"], 1);
        assert_eq!(summary["gc_flows"], 1);
        let remaining: i64 = conn
            .query_row("SELECT COUNT(*) FROM flow_runs", [], |r| r.get(0))
            .unwrap();
        assert_eq!(remaining, 1);
        let live_state: String = conn
            .query_row("SELECT state FROM flow_runs", [], |r| r.get(0))
            .unwrap();
        assert_eq!(live_state, "RUNNING");
        let orphans: i64 = conn
            .query_row("SELECT COUNT(*) FROM flows", [], |r| r.get(0))
            .unwrap();
        assert_eq!(orphans, 0);
        let tasks: i64 = conn
            .query_row("SELECT COUNT(*) FROM task_runs", [], |r| r.get(0))
            .unwrap();
        assert_eq!(tasks, 0);
    }

    #[test]
    fn ensure_canonical_inserts_and_rejects_alias() {
        let conn = memory_catalog();
        let created = ensure_flow_canonical(&conn, "alpha").unwrap();
        assert_eq!(created["ok"], true);
        let flow_id = created["flow"]["id"].as_str().unwrap().to_string();
        let again = ensure_flow_canonical(&conn, "alpha").unwrap();
        assert_eq!(again["flow"]["id"], flow_id);
        conn.execute(
            "INSERT INTO flow_aliases(name,flow_id,created_at) VALUES('old',?1,?2)",
            params![flow_id, "2026-01-01T00:00:00+00:00"],
        )
        .unwrap();
        let reserved = ensure_flow_canonical(&conn, "old").unwrap();
        assert_eq!(reserved["ok"], false);
        assert_eq!(reserved["error"]["code"], "alias_reserved");
    }

    #[test]
    fn attach_does_not_fork_when_name_is_only_an_alias() {
        let conn = memory_catalog();
        let now = "2026-01-01T00:00:00+00:00";
        conn.execute(
            "INSERT INTO flows(id,name,status,created_at,updated_at) VALUES('f1','beta','active',?1,?1)",
            params![now],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO flow_aliases(name,flow_id,created_at) VALUES('alpha','f1',?1)",
            params![now],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO flow_runs(id,name,state,version,created_at,updated_at,depth) \
             VALUES('r1','alpha','SCHEDULED',0,?1,?1,0)",
            params![now],
        )
        .unwrap();
        attach_flow_run_to_catalog(&conn, "r1", "alpha").unwrap();
        let catalogs: i64 = conn
            .query_row("SELECT COUNT(*) FROM flows", [], |r| r.get(0))
            .unwrap();
        assert_eq!(catalogs, 1);
        let flow_id: Option<String> = conn
            .query_row("SELECT flow_id FROM flow_runs WHERE id = 'r1'", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert!(flow_id.is_none());
    }
}
