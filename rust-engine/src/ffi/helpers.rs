use serde_json::{json, Value};

use crate::engine::{EngineError, SetStateResponse, TransitionStatus};

use super::EngineContext;

pub(crate) fn engine_error_value(err: EngineError) -> Value {
    match err {
        EngineError::MissingRun(id) => json!({
            "code": "missing_run",
            "message": err.to_string(),
            "run_id": id.to_string(),
        }),
        EngineError::MissingTask(id) => json!({
            "code": "missing_task",
            "message": err.to_string(),
            "task_run_id": id.to_string(),
        }),
        EngineError::InvalidTransition { from, to } => json!({
            "code": "invalid_transition",
            "message": err.to_string(),
            "from": from,
            "to": to,
        }),
        EngineError::VersionConflict { expected, actual } => json!({
            "code": "version_conflict",
            "message": err.to_string(),
            "expected": expected,
            "actual": actual,
        }),
    }
}

pub(crate) fn set_state_response_json(resp: &SetStateResponse) -> Value {
    let status = match resp.status {
        TransitionStatus::Applied => "applied",
        TransitionStatus::Duplicate => "duplicate",
    };
    json!({
        "ok": true,
        "status": status,
        "current_state": resp.current_state,
        "version": resp.version,
    })
}

pub(crate) fn resolve_db_path(ctx: &EngineContext, body: &Value) -> Result<String, String> {
    if let Some(path) = body.get("db_path").and_then(|v| v.as_str()) {
        return Ok(path.to_string());
    }
    ctx.db_path
        .clone()
        .ok_or_else(|| "missing db path (call bind_db or provide db_path)".to_string())
}

pub(crate) fn pg_fallback(op: &str) -> Value {
    json!({"ok": false, "error": {"code": "fallback", "message": format!("unknown control op: {op}")}})
}

pub(crate) fn uuid_from_field(body: &Value, key: &str) -> Result<uuid::Uuid, String> {
    let s = body
        .get(key)
        .and_then(|v| v.as_str())
        .ok_or_else(|| format!("missing string field {key}"))?;
    uuid::Uuid::parse_str(s).map_err(|e| e.to_string())
}

pub(crate) fn u64_from_field(body: &Value, key: &str) -> Result<u64, String> {
    body.get(key)
        .and_then(|v| v.as_u64())
        .ok_or_else(|| format!("missing u64 field {key}"))
}

pub(crate) fn state_from_field(body: &Value, key: &str) -> Result<crate::engine::RunState, String> {
    serde_json::from_value(
        body.get(key)
            .cloned()
            .ok_or_else(|| format!("missing field {key}"))?,
    )
    .map_err(|e| e.to_string())
}

pub(crate) fn opt_str_from_field(body: &Value, key: &str) -> Option<String> {
    body.get(key)
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn uuid_from_field_requires_string() {
        let err = uuid_from_field(&json!({}), "run_id").expect_err("missing");
        assert!(err.contains("missing string field run_id"), "{err}");
    }

    #[test]
    fn pg_fallback_code() {
        let v = pg_fallback("demo_op");
        assert_eq!(v["error"]["code"], "fallback");
        assert!(v["error"]["message"].as_str().unwrap().contains("demo_op"));
    }
}
