//! Flow-run list query: filters, sort, and keyset cursors for the UI runs table.
//! Hot path behind `ironflow_query(kind=flow_runs)`.

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rusqlite::{params_from_iter, Connection, ToSql};
use serde_json::{json, Value};

use crate::flow_catalog_ops::{catalog_status_predicate, column_exists, table_exists};

const DEFAULT_SORT: &str = "seq";
const DEFAULT_ORDER: &str = "desc";
const CURSOR_PREFIX: &str = "v1.";

#[derive(Debug, Clone)]
struct ListParams {
    state: Option<String>,
    flow_name: Option<String>,
    deployment_id: Option<String>,
    created_after: Option<String>,
    created_before: Option<String>,
    q: Option<String>,
    sort: String,
    order: String,
    cursor: Option<String>,
    limit: i64,
    hide_archived: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct CursorToken {
    field: String,
    order: String,
    key: Option<String>,
    seq: i64,
}

fn parse_limit(params: &Value, default_limit: i64) -> i64 {
    params
        .get("limit")
        .and_then(Value::as_i64)
        .filter(|v| *v > 0)
        .unwrap_or(default_limit)
        .min(500)
}

fn parse_opt_string(params: &Value, key: &str) -> Option<String> {
    params
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

fn parse_bool(params: &Value, key: &str) -> bool {
    match params.get(key) {
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_i64().unwrap_or(0) != 0,
        Some(Value::String(s)) => matches!(s.to_ascii_lowercase().as_str(), "1" | "true" | "yes"),
        _ => false,
    }
}

fn normalize_sort(raw: Option<String>) -> Result<String, String> {
    let sort = raw
        .unwrap_or_else(|| DEFAULT_SORT.to_string())
        .to_ascii_lowercase();
    match sort.as_str() {
        "seq" | "created_at" | "updated_at" | "name" | "state" => Ok(sort),
        other => Err(format!(
            "invalid sort {other:?}; expected seq|created_at|updated_at|name|state"
        )),
    }
}

fn normalize_order(raw: Option<String>) -> Result<String, String> {
    let order = raw
        .unwrap_or_else(|| DEFAULT_ORDER.to_string())
        .to_ascii_lowercase();
    match order.as_str() {
        "asc" | "desc" => Ok(order),
        other => Err(format!("invalid order {other:?}; expected asc|desc")),
    }
}

fn parse_list_params(params_json: &str) -> Result<ListParams, String> {
    let parsed: Value = serde_json::from_str(params_json).unwrap_or_else(|_| json!({}));
    Ok(ListParams {
        state: parse_opt_string(&parsed, "state"),
        flow_name: parse_opt_string(&parsed, "flow_name"),
        deployment_id: parse_opt_string(&parsed, "deployment_id"),
        created_after: parse_opt_string(&parsed, "created_after"),
        created_before: parse_opt_string(&parsed, "created_before"),
        q: parse_opt_string(&parsed, "q"),
        sort: normalize_sort(parse_opt_string(&parsed, "sort"))?,
        order: normalize_order(parse_opt_string(&parsed, "order"))?,
        cursor: parse_opt_string(&parsed, "cursor"),
        limit: parse_limit(&parsed, 50),
        hide_archived: parse_bool(&parsed, "hide_archived"),
    })
}

fn encode_cursor(token: &CursorToken) -> String {
    if token.field == DEFAULT_SORT && token.order == DEFAULT_ORDER && token.key.is_none() {
        return token.seq.to_string();
    }
    let payload = json!({
        "f": token.field,
        "o": token.order,
        "k": token.key,
        "s": token.seq,
    });
    let encoded = URL_SAFE_NO_PAD.encode(payload.to_string().as_bytes());
    format!("{CURSOR_PREFIX}{encoded}")
}

fn decode_cursor(raw: &str, sort: &str, order: &str) -> Result<CursorToken, String> {
    if let Ok(seq) = raw.parse::<i64>() {
        if sort != DEFAULT_SORT || order != DEFAULT_ORDER {
            return Err(
                "plain seq cursor is only valid with default sort=seq&order=desc; clear cursor when changing sort"
                    .into(),
            );
        }
        return Ok(CursorToken {
            field: DEFAULT_SORT.to_string(),
            order: DEFAULT_ORDER.to_string(),
            key: None,
            seq,
        });
    }
    let Some(encoded) = raw.strip_prefix(CURSOR_PREFIX) else {
        return Err("invalid cursor encoding".into());
    };
    let bytes = URL_SAFE_NO_PAD
        .decode(encoded.as_bytes())
        .map_err(|e| format!("invalid cursor: {e}"))?;
    let value: Value =
        serde_json::from_slice(&bytes).map_err(|e| format!("invalid cursor json: {e}"))?;
    let field = value
        .get("f")
        .and_then(Value::as_str)
        .ok_or_else(|| "cursor missing f".to_string())?
        .to_string();
    let cursor_order = value
        .get("o")
        .and_then(Value::as_str)
        .ok_or_else(|| "cursor missing o".to_string())?
        .to_string();
    if field != sort || cursor_order != order {
        return Err(
            "cursor sort/order does not match request; clear cursor when changing sort".into(),
        );
    }
    let seq = value
        .get("s")
        .and_then(Value::as_i64)
        .ok_or_else(|| "cursor missing s".to_string())?;
    let key = value.get("k").and_then(|v| {
        if v.is_null() {
            None
        } else {
            v.as_str().map(str::to_string)
        }
    });
    Ok(CursorToken {
        field,
        order: cursor_order,
        key,
        seq,
    })
}

fn like_pattern(q: &str) -> String {
    let escaped = q
        .replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_");
    format!("%{escaped}%")
}

fn sort_column(sort: &str) -> &'static str {
    match sort {
        "created_at" => "fr.created_at",
        "updated_at" => "fr.updated_at",
        "name" => "fr.name",
        "state" => "fr.state",
        _ => "fr.seq",
    }
}

fn keyset_predicate(sort: &str, order: &str) -> &'static str {
    let desc = order == "desc";
    match (sort, desc) {
        ("seq", true) => "fr.seq < ?",
        ("seq", false) => "fr.seq > ?",
        (_, true) => "(SORT_COL < ? OR (SORT_COL = ? AND fr.seq < ?))",
        (_, false) => "(SORT_COL > ? OR (SORT_COL = ? AND fr.seq > ?))",
    }
}

pub fn query_flow_runs(conn: &Connection, params_json: &str) -> Result<String, String> {
    let p = parse_list_params(params_json)?;
    let has_catalog = table_exists(conn, "flows") && column_exists(conn, "flow_runs", "flow_id");
    let has_flow_id = column_exists(conn, "flow_runs", "flow_id");
    let has_deployment_runs = table_exists(conn, "deployment_runs");

    let cursor_token = match p.cursor.as_deref() {
        Some(raw) => Some(decode_cursor(raw, &p.sort, &p.order)?),
        None => None,
    };

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

    let mut where_parts: Vec<String> = Vec::new();
    let mut binds: Vec<Box<dyn ToSql>> = Vec::new();

    if let Some(state) = &p.state {
        where_parts.push("fr.state = ?".into());
        binds.push(Box::new(state.clone()));
    }
    if let Some(flow_name) = &p.flow_name {
        where_parts.push("fr.name = ?".into());
        binds.push(Box::new(flow_name.clone()));
    }
    if let Some(q) = &p.q {
        where_parts.push("fr.name LIKE ? ESCAPE '\\'".into());
        binds.push(Box::new(like_pattern(q)));
    }
    if let Some(after) = &p.created_after {
        where_parts.push("fr.created_at >= ?".into());
        binds.push(Box::new(after.clone()));
    }
    if let Some(before) = &p.created_before {
        where_parts.push("fr.created_at <= ?".into());
        binds.push(Box::new(before.clone()));
    }
    if let Some(deployment_id) = &p.deployment_id {
        if !has_deployment_runs {
            return serde_json::to_string(&json!({"items": [], "next_cursor": null}))
                .map_err(|e| e.to_string());
        }
        where_parts.push(
            "EXISTS (SELECT 1 FROM deployment_runs dr WHERE dr.flow_run_id = fr.id AND dr.deployment_id = ?)"
                .into(),
        );
        binds.push(Box::new(deployment_id.clone()));
    }
    if has_catalog {
        where_parts.push(catalog_status_predicate(p.hide_archived).into());
    }

    if let Some(token) = &cursor_token {
        let pred = keyset_predicate(&p.sort, &p.order).replace("SORT_COL", sort_column(&p.sort));
        where_parts.push(pred);
        if p.sort == DEFAULT_SORT {
            binds.push(Box::new(token.seq));
        } else {
            let key = token
                .key
                .clone()
                .ok_or_else(|| "cursor missing sort key".to_string())?;
            binds.push(Box::new(key.clone()));
            binds.push(Box::new(key));
            binds.push(Box::new(token.seq));
        }
    }

    sql.push_str(" WHERE ");
    if where_parts.is_empty() {
        sql.push_str("1=1");
    } else {
        sql.push_str(&where_parts.join(" AND "));
    }

    let order_sql = if p.sort == DEFAULT_SORT {
        format!(" ORDER BY fr.seq {} LIMIT ?", p.order.to_ascii_uppercase())
    } else {
        format!(
            " ORDER BY {} {}, fr.seq {} LIMIT ?",
            sort_column(&p.sort),
            p.order.to_ascii_uppercase(),
            p.order.to_ascii_uppercase()
        )
    };
    sql.push_str(&order_sql);
    binds.push(Box::new(p.limit));

    let param_refs: Vec<&dyn ToSql> = binds.iter().map(|b| b.as_ref()).collect();
    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
    let items = stmt
        .query_map(params_from_iter(param_refs), |row| {
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
                "seq": row.get::<_, i64>(0)?,
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

    page_with_sort_cursor(items, p.limit, &p.sort, &p.order)
}

fn page_with_sort_cursor(
    mut items: Vec<Value>,
    limit: i64,
    sort: &str,
    order: &str,
) -> Result<String, String> {
    let next_cursor = if items.len() as i64 == limit {
        items.last().and_then(|it| {
            let seq = it.get("seq").and_then(Value::as_i64)?;
            let key = if sort == DEFAULT_SORT {
                None
            } else {
                let field = match sort {
                    "created_at" => "created_at",
                    "updated_at" => "updated_at",
                    "name" => "name",
                    "state" => "state",
                    _ => return None,
                };
                Some(it.get(field)?.as_str()?.to_string())
            };
            Some(encode_cursor(&CursorToken {
                field: sort.to_string(),
                order: order.to_string(),
                key,
                seq,
            }))
        })
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

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::params;

    fn memory_db() -> Connection {
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
            CREATE TABLE flows (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT,
                deleted_at TEXT
            );
            CREATE TABLE deployment_runs (
                id TEXT PRIMARY KEY,
                deployment_id TEXT NOT NULL,
                flow_run_id TEXT,
                created_at TEXT NOT NULL
            );",
        )
        .expect("schema");
        conn
    }

    fn insert_run(
        conn: &Connection,
        id: &str,
        name: &str,
        state: &str,
        created: &str,
        updated: &str,
        flow_id: Option<&str>,
    ) {
        conn.execute(
            "INSERT INTO flow_runs(id,name,state,version,created_at,updated_at,depth,flow_id) \
             VALUES(?1,?2,?3,0,?4,?5,0,?6)",
            params![id, name, state, created, updated, flow_id],
        )
        .unwrap();
    }

    #[test]
    fn filters_and_sort_name_asc_with_composite_cursor() {
        let conn = memory_db();
        insert_run(
            &conn,
            "r1",
            "alpha",
            "COMPLETED",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            None,
        );
        insert_run(
            &conn,
            "r2",
            "beta",
            "FAILED",
            "2026-01-02T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
            None,
        );
        insert_run(
            &conn,
            "r3",
            "gamma",
            "COMPLETED",
            "2026-01-03T00:00:00+00:00",
            "2026-01-03T00:00:00+00:00",
            None,
        );
        let page1: Value = serde_json::from_str(
            &query_flow_runs(&conn, r#"{"sort":"name","order":"asc","limit":2}"#).unwrap(),
        )
        .unwrap();
        let ids1: Vec<&str> = page1["items"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids1, vec!["r1", "r2"]);
        let cursor = page1["next_cursor"].as_str().unwrap();
        assert!(cursor.starts_with(CURSOR_PREFIX));
        let page2: Value = serde_json::from_str(
            &query_flow_runs(
                &conn,
                &format!(r#"{{"sort":"name","order":"asc","limit":2,"cursor":"{cursor}"}}"#),
            )
            .unwrap(),
        )
        .unwrap();
        let ids2: Vec<&str> = page2["items"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids2, vec!["r3"]);
        assert!(page2["next_cursor"].is_null());
    }

    #[test]
    fn q_and_state_and_created_range() {
        let conn = memory_db();
        insert_run(
            &conn,
            "r1",
            "demo_flow",
            "COMPLETED",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            None,
        );
        insert_run(
            &conn,
            "r2",
            "other",
            "FAILED",
            "2026-01-02T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
            None,
        );
        insert_run(
            &conn,
            "r3",
            "demo_flow",
            "FAILED",
            "2026-01-03T00:00:00+00:00",
            "2026-01-03T00:00:00+00:00",
            None,
        );
        let payload: Value = serde_json::from_str(
            &query_flow_runs(
                &conn,
                r#"{"q":"demo","state":"FAILED","created_after":"2026-01-02T00:00:00+00:00","limit":50}"#,
            )
            .unwrap(),
        )
        .unwrap();
        let ids: Vec<&str> = payload["items"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids, vec!["r3"]);
    }

    #[test]
    fn deployment_id_filter_via_exists() {
        let conn = memory_db();
        insert_run(
            &conn,
            "r1",
            "a",
            "COMPLETED",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            None,
        );
        insert_run(
            &conn,
            "r2",
            "b",
            "COMPLETED",
            "2026-01-02T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
            None,
        );
        conn.execute(
            "INSERT INTO deployment_runs(id,deployment_id,flow_run_id,created_at) \
             VALUES('dr1','dep-1','r2','2026-01-02T00:00:00+00:00')",
            [],
        )
        .unwrap();
        let payload: Value = serde_json::from_str(
            &query_flow_runs(&conn, r#"{"deployment_id":"dep-1","limit":50}"#).unwrap(),
        )
        .unwrap();
        assert_eq!(payload["items"].as_array().unwrap().len(), 1);
        assert_eq!(payload["items"][0]["id"], "r2");
    }

    #[test]
    fn legacy_seq_cursor_still_works() {
        let conn = memory_db();
        for i in 1..=3 {
            insert_run(
                &conn,
                &format!("r{i}"),
                "n",
                "COMPLETED",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                None,
            );
        }
        let page1: Value =
            serde_json::from_str(&query_flow_runs(&conn, r#"{"limit":2}"#).unwrap()).unwrap();
        let cursor = page1["next_cursor"].as_str().unwrap();
        assert!(cursor.parse::<i64>().is_ok());
        let page2: Value = serde_json::from_str(
            &query_flow_runs(&conn, &format!(r#"{{"limit":2,"cursor":"{cursor}"}}"#)).unwrap(),
        )
        .unwrap();
        assert_eq!(page2["items"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn cursor_sort_mismatch_errors() {
        let err = query_flow_runs(
            &memory_db(),
            r#"{"sort":"name","order":"asc","cursor":"12","limit":10}"#,
        )
        .unwrap_err();
        assert!(err.contains("plain seq cursor"));
    }

    #[test]
    fn hides_archived_catalog() {
        let conn = memory_db();
        let now = "2026-01-01T00:00:00+00:00";
        conn.execute(
            "INSERT INTO flows(id,name,status,created_at,updated_at,archived_at) \
             VALUES('f-arch','old','archived',?1,?1,?1)",
            params![now],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO flows(id,name,status,created_at,updated_at) \
             VALUES('f-live','live','active',?1,?1)",
            params![now],
        )
        .unwrap();
        insert_run(&conn, "r1", "old", "COMPLETED", now, now, Some("f-arch"));
        insert_run(&conn, "r2", "live", "COMPLETED", now, now, Some("f-live"));
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
    }
}
