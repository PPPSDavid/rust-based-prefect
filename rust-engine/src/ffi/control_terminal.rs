use serde_json::Value;

use super::EngineContext;

pub(crate) fn handle(ctx: &mut EngineContext, op: &str, body: &Value) -> Result<Value, String> {
    match op {
        "resolve_flow_terminal_state" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "resolve_flow_terminal_state requires bind_db".to_string())?;
            crate::flow_terminal_ops::resolve_flow_terminal_state(conn, body)
        }
        "list_contributing_children" => {
            let conn = ctx
                .db_conn
                .as_ref()
                .ok_or_else(|| "list_contributing_children requires bind_db".to_string())?;
            crate::flow_terminal_ops::list_contributing_children(conn, body)
        }
        _ => Err(format!("unknown control op: {op}")),
    }
}
