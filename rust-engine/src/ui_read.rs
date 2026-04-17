use rusqlite::{params, Connection};
use serde_json::{json, Value};

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

pub fn query(db_path: &str, kind: &str, params_json: &str) -> Result<String, String> {
    let conn = Connection::open(db_path).map_err(|e| e.to_string())?;
    match kind {
        "flow_runs" => query_flow_runs(&conn, params_json),
        "flow_run_detail" => query_flow_run_detail(&conn, params_json),
        "task_runs" => query_task_runs(&conn, params_json),
        "logs" => query_logs(&conn, params_json),
        "flows" => query_flows(&conn, params_json),
        "tasks" => query_tasks(&conn, params_json),
        "events" => query_events(&conn, params_json),
        "artifacts_flow" => query_artifacts_flow(&conn, params_json),
        "artifacts_task" => query_artifacts_task(&conn, params_json),
        "artifact" => query_artifact(&conn, params_json),
        _ => Err(format!("unknown query kind: {kind}")),
    }
}

fn query_flow_runs(conn: &Connection, params_json: &str) -> Result<String, String> {
    let state = parse_opt_string(params_json, "state");
    let cursor = parse_opt_string(params_json, "cursor").and_then(|v| v.parse::<i64>().ok());
    let limit = parse_limit(params_json, 50);
    let mut sql =
        "SELECT seq,id,name,state,version,created_at,updated_at FROM flow_runs".to_string();
    let mut has_where = false;
    if state.is_some() {
        sql.push_str(" WHERE state = ?1");
        has_where = true;
    }
    if cursor.is_some() {
        sql.push_str(if has_where { " AND seq < ?2" } else { " WHERE seq < ?2" });
    }
    sql.push_str(" ORDER BY seq DESC LIMIT ?3");

    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
    let items = stmt
        .query_map(
            params![
                state.as_deref(),
                cursor,
                limit
            ],
            |row| {
                Ok(json!({
                    "id": row.get::<_, String>(1)?,
                    "name": row.get::<_, String>(2)?,
                    "state": row.get::<_, String>(3)?,
                    "version": row.get::<_, i64>(4)?,
                    "created_at": row.get::<_, String>(5)?,
                    "updated_at": row.get::<_, String>(6)?,
                    "seq": row.get::<_, i64>(0)?
                }))
            },
        )
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;

    page_with_cursor(items, limit)
}

fn query_flow_run_detail(conn: &Connection, params_json: &str) -> Result<String, String> {
    let flow_run_id = parse_opt_string(params_json, "flow_run_id")
        .ok_or_else(|| "flow_run_id required".to_string())?;
    let mut stmt = conn
        .prepare("SELECT id,name,state,version,created_at,updated_at FROM flow_runs WHERE id = ?1 LIMIT 1")
        .map_err(|e| e.to_string())?;
    let mut rows = stmt.query(params![flow_run_id]).map_err(|e| e.to_string())?;
    if let Some(row) = rows.next().map_err(|e| e.to_string())? {
        serde_json::to_string(&json!({
            "id": row.get::<_, String>(0).map_err(|e| e.to_string())?,
            "name": row.get::<_, String>(1).map_err(|e| e.to_string())?,
            "state": row.get::<_, String>(2).map_err(|e| e.to_string())?,
            "version": row.get::<_, i64>(3).map_err(|e| e.to_string())?,
            "created_at": row.get::<_, String>(4).map_err(|e| e.to_string())?,
            "updated_at": row.get::<_, String>(5).map_err(|e| e.to_string())?
        }))
        .map_err(|e| e.to_string())
    } else {
        Ok("null".to_string())
    }
}

fn query_task_runs(conn: &Connection, params_json: &str) -> Result<String, String> {
    let flow_run_id = parse_opt_string(params_json, "flow_run_id")
        .ok_or_else(|| "flow_run_id required".to_string())?;
    let cursor = parse_opt_string(params_json, "cursor").and_then(|v| v.parse::<i64>().ok());
    let limit = parse_limit(params_json, 200);
    let mut sql = "SELECT seq,id,flow_run_id,task_name,planned_node_id,state,version,created_at,updated_at FROM task_runs WHERE flow_run_id = ?1".to_string();
    if cursor.is_some() {
        sql.push_str(" AND seq < ?2");
    }
    sql.push_str(" ORDER BY seq DESC LIMIT ?3");
    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
    let items = stmt
        .query_map(params![flow_run_id, cursor, limit], |row| {
            Ok(json!({
                "id": row.get::<_, String>(1)?,
                "flow_run_id": row.get::<_, String>(2)?,
                "task_name": row.get::<_, String>(3)?,
                "planned_node_id": row.get::<_, Option<String>>(4)?,
                "state": row.get::<_, String>(5)?,
                "version": row.get::<_, i64>(6)?,
                "created_at": row.get::<_, String>(7)?,
                "updated_at": row.get::<_, String>(8)?,
                "seq": row.get::<_, i64>(0)?
            }))
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    page_with_cursor(items, limit)
}

fn query_logs(conn: &Connection, params_json: &str) -> Result<String, String> {
    let flow_run_id = parse_opt_string(params_json, "flow_run_id")
        .ok_or_else(|| "flow_run_id required".to_string())?;
    let task_run_id = parse_opt_string(params_json, "task_run_id");
    let level = parse_opt_string(params_json, "level");
    let cursor = parse_opt_string(params_json, "cursor").and_then(|v| v.parse::<i64>().ok());
    let limit = parse_limit(params_json, 500);

    let mut sql = "SELECT seq,id,flow_run_id,task_run_id,level,message,timestamp FROM logs WHERE flow_run_id = ?1".to_string();
    sql.push_str(" AND (?2 IS NULL OR task_run_id = ?2)");
    sql.push_str(" AND (?3 IS NULL OR level = ?3)");
    if cursor.is_some() {
        sql.push_str(" AND seq < ?4");
    }
    sql.push_str(" ORDER BY seq DESC LIMIT ?5");

    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
    let items = stmt
        .query_map(params![flow_run_id, task_run_id, level, cursor, limit], |row| {
            Ok(json!({
                "id": row.get::<_, String>(1)?,
                "flow_run_id": row.get::<_, String>(2)?,
                "task_run_id": row.get::<_, Option<String>>(3)?,
                "level": row.get::<_, String>(4)?,
                "message": row.get::<_, String>(5)?,
                "timestamp": row.get::<_, String>(6)?,
                "seq": row.get::<_, i64>(0)?
            }))
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    page_with_cursor(items, limit)
}

fn query_flows(conn: &Connection, params_json: &str) -> Result<String, String> {
    let limit = parse_limit(params_json, 200);
    let mut stmt = conn
        .prepare(
            "SELECT name,MAX(updated_at) AS updated_at,COUNT(*) AS run_count FROM flow_runs GROUP BY name ORDER BY updated_at DESC LIMIT ?1",
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
    serde_json::to_string(&json!({"items": items, "next_cursor": null})).map_err(|e| e.to_string())
}

fn query_tasks(conn: &Connection, params_json: &str) -> Result<String, String> {
    let flow_name = parse_opt_string(params_json, "flow_name");
    let limit = parse_limit(params_json, 200);
    let sql = "SELECT tr.task_name,COUNT(*) AS run_count,MAX(tr.updated_at) AS updated_at
               FROM task_runs tr
               JOIN flow_runs fr ON fr.id = tr.flow_run_id
               WHERE (?1 IS NULL OR fr.name = ?1)
               GROUP BY tr.task_name
               ORDER BY updated_at DESC
               LIMIT ?2";
    let mut stmt = conn.prepare(sql).map_err(|e| e.to_string())?;
    let items = stmt
        .query_map(params![flow_name, limit], |row| {
            Ok(json!({
                "task_name": row.get::<_, String>(0)?,
                "run_count": row.get::<_, i64>(1)?,
                "updated_at": row.get::<_, String>(2)?
            }))
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    serde_json::to_string(&items).map_err(|e| e.to_string())
}

fn query_events(conn: &Connection, params_json: &str) -> Result<String, String> {
    let flow_run_id = parse_opt_string(params_json, "flow_run_id")
        .ok_or_else(|| "flow_run_id required".to_string())?;
    let cursor = parse_opt_string(params_json, "cursor").and_then(|v| v.parse::<i64>().ok());
    let limit = parse_limit(params_json, 500);
    let mut sql = "SELECT seq,event_id,run_id,task_run_id,from_state,to_state,event_type,kind,data,timestamp FROM events WHERE run_id = ?1".to_string();
    if cursor.is_some() {
        sql.push_str(" AND seq < ?2");
    }
    sql.push_str(" ORDER BY seq DESC LIMIT ?3");
    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
    let items = stmt
        .query_map(params![flow_run_id, cursor, limit], |row| {
            let data_raw: Option<String> = row.get(8)?;
            Ok(json!({
                "event_id": row.get::<_, String>(1)?,
                "run_id": row.get::<_, String>(2)?,
                "task_run_id": row.get::<_, Option<String>>(3)?,
                "from_state": row.get::<_, Option<String>>(4)?,
                "to_state": row.get::<_, Option<String>>(5)?,
                "event_type": row.get::<_, Option<String>>(6)?,
                "kind": row.get::<_, Option<String>>(7)?,
                "data": serde_json::from_str::<Value>(&data_raw.unwrap_or_else(|| "{}".to_string())).unwrap_or_else(|_| json!({})),
                "timestamp": row.get::<_, String>(9)?,
                "seq": row.get::<_, i64>(0)?
            }))
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    page_with_cursor(items, limit)
}

fn query_artifacts_flow(conn: &Connection, params_json: &str) -> Result<String, String> {
    query_artifacts(conn, params_json, "flow_run_id")
}

fn query_artifacts_task(conn: &Connection, params_json: &str) -> Result<String, String> {
    query_artifacts(conn, params_json, "task_run_id")
}

fn query_artifacts(conn: &Connection, params_json: &str, key: &str) -> Result<String, String> {
    let value = parse_opt_string(params_json, key).ok_or_else(|| format!("{key} required"))?;
    let limit = parse_limit(params_json, 200);
    let sql = format!("SELECT id,flow_run_id,task_run_id,artifact_type,key,summary,created_at FROM artifacts WHERE {key} = ?1 ORDER BY created_at DESC LIMIT ?2");
    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
    let items = stmt
        .query_map(params![value, limit], |row| {
            Ok(json!({
                "id": row.get::<_, String>(0)?,
                "flow_run_id": row.get::<_, String>(1)?,
                "task_run_id": row.get::<_, Option<String>>(2)?,
                "artifact_type": row.get::<_, String>(3)?,
                "key": row.get::<_, String>(4)?,
                "summary": row.get::<_, Option<String>>(5)?,
                "created_at": row.get::<_, String>(6)?
            }))
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    serde_json::to_string(&items).map_err(|e| e.to_string())
}

fn query_artifact(conn: &Connection, params_json: &str) -> Result<String, String> {
    let artifact_id =
        parse_opt_string(params_json, "artifact_id").ok_or_else(|| "artifact_id required".to_string())?;
    let mut stmt = conn
        .prepare(
            "SELECT id,flow_run_id,task_run_id,artifact_type,key,summary,created_at FROM artifacts WHERE id = ?1 LIMIT 1",
        )
        .map_err(|e| e.to_string())?;
    let mut rows = stmt.query(params![artifact_id]).map_err(|e| e.to_string())?;
    if let Some(row) = rows.next().map_err(|e| e.to_string())? {
        serde_json::to_string(&json!({
            "id": row.get::<_, String>(0).map_err(|e| e.to_string())?,
            "flow_run_id": row.get::<_, String>(1).map_err(|e| e.to_string())?,
            "task_run_id": row.get::<_, Option<String>>(2).map_err(|e| e.to_string())?,
            "artifact_type": row.get::<_, String>(3).map_err(|e| e.to_string())?,
            "key": row.get::<_, String>(4).map_err(|e| e.to_string())?,
            "summary": row.get::<_, Option<String>>(5).map_err(|e| e.to_string())?,
            "created_at": row.get::<_, String>(6).map_err(|e| e.to_string())?
        }))
        .map_err(|e| e.to_string())
    } else {
        Ok("null".to_string())
    }
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
