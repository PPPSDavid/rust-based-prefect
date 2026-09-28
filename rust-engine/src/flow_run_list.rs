//! Server-side flow-run list: state, text search, created-at window, cursor page.
//!
//! Python `control_plane.flow_run_list` mirrors this SQL. Keep both in parity.

use rusqlite::{params_from_iter, types::ToSql, Connection};
use serde_json::{json, Value};

use crate::flow_catalog_ops::{catalog_status_predicate, column_exists, table_exists};

struct ListArgs {
    state: Option<String>,
    cursor: Option<i64>,
    like: Option<String>,
    created_after: Option<String>,
    created_before: Option<String>,
    limit: i64,
}

pub fn query_flow_runs(conn: &Connection, params_json: &str) -> Result<String, String> {
    let args = parse_args(params_json)?;
    let has_catalog = table_exists(conn, "flows") && column_exists(conn, "flow_runs", "flow_id");
    let has_flow_id = column_exists(conn, "flow_runs", "flow_id");
    let has_tags = column_exists(conn, "flow_runs", "tags");
    let has_deployments =
        table_exists(conn, "deployment_runs") && table_exists(conn, "deployments");
    let hide_archived = parse_bool(params_json, "hide_archived");
    let sql = build_sql(
        has_catalog,
        has_flow_id,
        has_tags,
        has_deployments,
        hide_archived,
    );
    let params = bind_params(&args);
    let refs: Vec<&dyn ToSql> = params.iter().map(|item| item as &dyn ToSql).collect();
    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
    let items = stmt
        .query_map(params_from_iter(refs), row_to_json)
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    page_flow_runs(items, args.limit)
}

/// Cursor is the last returned seq only when a further matching row exists.
fn page_flow_runs(mut items: Vec<Value>, limit: i64) -> Result<String, String> {
    let has_more = items.len() as i64 > limit;
    if has_more {
        items.truncate(limit as usize);
    }
    let next_cursor = if has_more {
        items
            .last()
            .and_then(|item| item.get("seq"))
            .and_then(Value::as_i64)
            .map(|seq| seq.to_string())
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

fn bind_params(args: &ListArgs) -> Vec<SqlValue> {
    vec![
        SqlValue::Text(args.state.clone()),
        SqlValue::Integer(args.cursor),
        SqlValue::Text(args.like.clone()),
        SqlValue::Text(args.like.clone()),
        SqlValue::Text(args.like.clone()),
        SqlValue::Text(args.created_after.clone()),
        SqlValue::Text(args.created_before.clone()),
        SqlValue::Limit(args.limit.saturating_add(1)),
    ]
}

enum SqlValue {
    Text(Option<String>),
    Integer(Option<i64>),
    Limit(i64),
}

impl ToSql for SqlValue {
    fn to_sql(&self) -> rusqlite::Result<rusqlite::types::ToSqlOutput<'_>> {
        match self {
            SqlValue::Text(value) => value.to_sql(),
            SqlValue::Integer(value) => value.to_sql(),
            SqlValue::Limit(value) => value.to_sql(),
        }
    }
}

fn parse_args(params_json: &str) -> Result<ListArgs, String> {
    let cursor = match opt_string(params_json, "cursor") {
        Some(raw) => Some(
            raw.parse::<i64>()
                .map_err(|_| "cursor must be an integer seq".to_string())?,
        ),
        None => None,
    };
    let q = opt_string(params_json, "q");
    if q.as_ref().is_some_and(|text| text.chars().count() > 200) {
        return Err("q must be at most 200 characters".to_string());
    }
    Ok(ListArgs {
        state: opt_string(params_json, "state"),
        cursor,
        like: q.as_deref().map(like_contains),
        created_after: opt_string(params_json, "created_after")
            .map(|value| normalize_timestamp(&value)),
        created_before: opt_string(params_json, "created_before")
            .map(|value| normalize_timestamp(&value)),
        limit: parse_limit(params_json, 50),
    })
}

fn build_sql(
    has_catalog: bool,
    has_flow_id: bool,
    has_tags: bool,
    has_deployments: bool,
    hide_archived: bool,
) -> String {
    let flow_id_expr = if has_flow_id { "fr.flow_id" } else { "NULL" };
    let flow_name_expr = if has_catalog {
        "COALESCE(catalog.name, fr.name)"
    } else {
        "fr.name"
    };
    let tags_expr = if has_tags { "fr.tags" } else { "NULL" };
    let mut sql = format!(
        "SELECT fr.seq, fr.id, fr.name, fr.state, fr.version, fr.created_at, fr.updated_at, \
         fr.parent_flow_run_id, fr.parent_task_run_id, fr.root_flow_run_id, \
         fr.execution_mode, fr.depth, {flow_id_expr} AS flow_id, {flow_name_expr} AS flow_name, \
         {tags_expr} AS tags, "
    );
    if has_deployments {
        sql.push_str(
            "latest.deployment_id AS deployment_id, dep.name AS deployment_name, \
             latest.started_at AS start_time, latest.finished_at AS end_time ",
        );
    } else {
        sql.push_str(
            "NULL AS deployment_id, NULL AS deployment_name, \
             NULL AS start_time, NULL AS end_time ",
        );
    }
    sql.push_str("FROM flow_runs fr");
    if has_catalog {
        sql.push_str(" LEFT JOIN flows catalog ON catalog.id = fr.flow_id");
    }
    if has_deployments {
        sql.push_str(
            " LEFT JOIN ( \
                SELECT dr.flow_run_id, dr.deployment_id, dr.started_at, dr.finished_at \
                FROM deployment_runs dr \
                INNER JOIN ( \
                    SELECT flow_run_id, MAX(seq) AS seq FROM deployment_runs \
                    WHERE flow_run_id IS NOT NULL GROUP BY flow_run_id \
                ) pick ON pick.flow_run_id = dr.flow_run_id AND pick.seq = dr.seq \
            ) latest ON latest.flow_run_id = fr.id \
            LEFT JOIN deployments dep ON dep.id = latest.deployment_id",
        );
    }
    sql.push_str(
        " WHERE (?1 IS NULL OR fr.state = ?1) \
         AND (?2 IS NULL OR fr.seq < ?2) \
         AND (?3 IS NULL OR ( \
            LOWER(fr.name) LIKE ?3 ESCAPE '\\' OR \
            LOWER(",
    );
    sql.push_str(flow_name_expr);
    sql.push_str(
        ") LIKE ?4 ESCAPE '\\' OR \
            LOWER(COALESCE(",
    );
    if has_deployments {
        sql.push_str("dep.name");
    } else {
        sql.push_str("''");
    }
    sql.push_str(
        ", '')) LIKE ?5 ESCAPE '\\' OR \
            LOWER(COALESCE(",
    );
    sql.push_str(tags_expr);
    sql.push_str(
        ", '')) LIKE ?3 ESCAPE '\\' \
         )) \
         AND (?6 IS NULL OR fr.created_at >= ?6) \
         AND (?7 IS NULL OR fr.created_at < ?7)",
    );
    if has_catalog {
        sql.push_str(" AND ");
        sql.push_str(catalog_status_predicate(hide_archived));
    }
    sql.push_str(" ORDER BY fr.seq DESC LIMIT ?8");
    sql
}

fn row_to_json(row: &rusqlite::Row<'_>) -> rusqlite::Result<Value> {
    Ok(json!({
        "seq": row.get::<_, i64>(0)?,
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
        "flow_id": row.get::<_, Option<String>>(12)?,
        "flow_name": row.get::<_, String>(13)?,
        "tags": parse_tags(row.get::<_, Option<String>>(14)?),
        "deployment_id": row.get::<_, Option<String>>(15)?,
        "deployment_name": row.get::<_, Option<String>>(16)?,
        "start_time": row.get::<_, Option<String>>(17)?,
        "end_time": row.get::<_, Option<String>>(18)?,
    }))
}

fn parse_tags(raw: Option<String>) -> Value {
    let Some(raw) = raw.filter(|text| !text.is_empty()) else {
        return json!([]);
    };
    serde_json::from_str::<Vec<String>>(&raw).map_or(json!([]), |tags| json!(tags))
}

fn like_contains(raw: &str) -> String {
    let mut escaped = String::new();
    for ch in raw.chars() {
        if ch == '\\' || ch == '%' || ch == '_' {
            escaped.push('\\');
        }
        escaped.push(ch);
    }
    format!("%{}%", escaped.to_lowercase())
}

fn normalize_timestamp(raw: &str) -> String {
    let text = raw.trim();
    if let Some(stripped) = text.strip_suffix('Z') {
        format!("{stripped}+00:00")
    } else {
        text.to_string()
    }
}

fn opt_string(params_json: &str, key: &str) -> Option<String> {
    let parsed: Value = serde_json::from_str(params_json).unwrap_or_else(|_| json!({}));
    parsed
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .map(str::to_string)
}

fn parse_limit(params_json: &str, default_limit: i64) -> i64 {
    let parsed: Value = serde_json::from_str(params_json).unwrap_or_else(|_| json!({}));
    parsed
        .get("limit")
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
        .unwrap_or(default_limit)
}

fn parse_bool(params_json: &str, key: &str) -> bool {
    let parsed: Value = serde_json::from_str(params_json).unwrap_or_else(|_| json!({}));
    match parsed.get(key) {
        Some(Value::Bool(flag)) => *flag,
        Some(Value::Number(n)) => n.as_i64().unwrap_or(0) != 0,
        Some(Value::String(s)) => matches!(s.to_ascii_lowercase().as_str(), "1" | "true" | "yes"),
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn schema() -> Connection {
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
                flow_id TEXT,
                tags TEXT
            );
            CREATE TABLE flows (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE deployments (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE deployment_runs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                deployment_id TEXT NOT NULL,
                flow_run_id TEXT,
                started_at TEXT,
                finished_at TEXT
            );",
        )
        .expect("schema");
        conn
    }

    fn insert_run(conn: &Connection, id: &str, name: &str, state: &str, created: &str, tags: &str) {
        conn.execute(
            "INSERT INTO flow_runs(id,name,state,version,created_at,updated_at,depth,flow_id,tags) \
             VALUES(?1,?2,?3,0,?4,?4,0,'f1',?5)",
            rusqlite::params![id, name, state, created, tags],
        )
        .unwrap();
    }

    #[test]
    fn search_and_cursor_skip_non_matches() {
        let conn = schema();
        conn.execute(
            "INSERT INTO flows(id,name,status) VALUES('f1','catalog-flow','active')",
            [],
        )
        .unwrap();
        insert_run(
            &conn,
            "old",
            "needle-flow",
            "FAILED",
            "2020-01-01T00:00:00+00:00",
            "[\"nightly\"]",
        );
        insert_run(
            &conn,
            "mid",
            "noise-a",
            "COMPLETED",
            "2024-01-01T00:00:00+00:00",
            "[]",
        );
        insert_run(
            &conn,
            "new",
            "noise-b",
            "RUNNING",
            "2026-01-01T00:00:00+00:00",
            "[]",
        );
        let page: Value = serde_json::from_str(
            &query_flow_runs(&conn, r#"{"q":"needle","limit":1,"hide_archived":true}"#).unwrap(),
        )
        .unwrap();
        assert_eq!(page["items"][0]["id"], "old");
        assert_eq!(page["items"][0]["flow_name"], "catalog-flow");
        assert_eq!(page["items"][0]["tags"][0], "nightly");
        assert!(page["next_cursor"].is_null());

        let noises: Value = serde_json::from_str(
            &query_flow_runs(&conn, r#"{"q":"noise","limit":1,"hide_archived":true}"#).unwrap(),
        )
        .unwrap();
        assert_eq!(noises["items"][0]["id"], "new");
        let cursor = noises["next_cursor"].as_str().unwrap();
        let second: Value = serde_json::from_str(
            &query_flow_runs(
                &conn,
                &format!(r#"{{"q":"noise","limit":1,"cursor":"{cursor}","hide_archived":true}}"#),
            )
            .unwrap(),
        )
        .unwrap();
        assert_eq!(second["items"][0]["id"], "mid");
    }

    #[test]
    fn deployment_window_and_literal_like() {
        let conn = schema();
        insert_run(
            &conn,
            "pct",
            "100%_done",
            "COMPLETED",
            "2026-06-01T00:00:00+00:00",
            "[]",
        );
        insert_run(
            &conn,
            "wild",
            "100Xdone",
            "COMPLETED",
            "2026-06-01T00:00:00+00:00",
            "[]",
        );
        conn.execute(
            "INSERT INTO deployments(id,name) VALUES('d1','alpha-deploy')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO deployment_runs(id,deployment_id,flow_run_id,started_at,finished_at) \
             VALUES('dr1','d1','pct','2026-06-01T00:00:01+00:00','2026-06-01T00:00:03+00:00')",
            [],
        )
        .unwrap();
        let literal: Value =
            serde_json::from_str(&query_flow_runs(&conn, r#"{"q":"100%_","limit":10}"#).unwrap())
                .unwrap();
        let ids: Vec<&str> = literal["items"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids, vec!["pct"]);
        assert_eq!(literal["items"][0]["deployment_name"], "alpha-deploy");
        assert_eq!(
            literal["items"][0]["start_time"],
            "2026-06-01T00:00:01+00:00"
        );
        assert_eq!(literal["items"][0]["end_time"], "2026-06-01T00:00:03+00:00");

        let by_dep: Value = serde_json::from_str(
            &query_flow_runs(&conn, r#"{"q":"alpha-deploy","limit":10}"#).unwrap(),
        )
        .unwrap();
        assert_eq!(by_dep["items"][0]["id"], "pct");

        let early: Value = serde_json::from_str(
            &query_flow_runs(
                &conn,
                r#"{"created_before":"2020-01-01T00:00:00Z","limit":10}"#,
            )
            .unwrap(),
        )
        .unwrap();
        assert!(early["items"].as_array().unwrap().is_empty());
    }
}
