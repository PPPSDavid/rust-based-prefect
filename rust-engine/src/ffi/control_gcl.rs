use serde_json::{json, Value};

use crate::concurrency_ops;

use super::EngineContext;

pub(crate) fn handle(ctx: &mut EngineContext, op: &str, body: &Value) -> Result<Value, String> {
    match op {
        "gcl_upsert" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "gcl_upsert requires bind_db".to_string())?;
            let lim = concurrency_ops::upsert_limit(conn, body)?;
            Ok(json!({"ok": true, "limit": lim}))
        }
        "gcl_delete" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "gcl_delete requires bind_db".to_string())?;
            let name = body
                .get("name")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing name".to_string())?;
            concurrency_ops::delete_limit(conn, name)
        }
        "gcl_get" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "gcl_get requires bind_db".to_string())?;
            let name = body
                .get("name")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing name".to_string())?;
            concurrency_ops::get_limit(conn, name)
        }
        "gcl_list" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "gcl_list requires bind_db".to_string())?;
            concurrency_ops::list_limits(conn)
        }
        "gcl_acquire" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "gcl_acquire requires bind_db".to_string())?;
            concurrency_ops::acquire(conn, body)
        }
        "gcl_release" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "gcl_release requires bind_db".to_string())?;
            concurrency_ops::release(conn, body)
        }
        "gcl_release_by_holders" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "gcl_release_by_holders requires bind_db".to_string())?;
            concurrency_ops::release_by_holders(conn, body)
        }
        "gcl_renew" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "gcl_renew requires bind_db".to_string())?;
            concurrency_ops::renew(conn, body)
        }
        "gcl_reclaim_expired" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "gcl_reclaim_expired requires bind_db".to_string())?;
            let now = body.get("now").and_then(|v| v.as_str());
            let n = concurrency_ops::reclaim_expired(conn, now)?;
            Ok(json!({"ok": true, "reclaimed": n}))
        }
        _ => Err(format!("unknown control op: {op}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::Engine;
    use serde_json::json;

    #[test]
    fn unknown_gcl_op() {
        let mut ctx = EngineContext {
            engine: Engine::new(),
            db_path: None,
            db_conn: None,
            pg_client: None,
        };
        let err = handle(&mut ctx, "gcl_not_real", &json!({})).expect_err("unknown");
        assert!(err.contains("unknown control op"), "{err}");
    }
}
