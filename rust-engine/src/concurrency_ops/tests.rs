use super::*;
use rusqlite::Connection;
use serde_json::json;

fn open_db() -> Connection {
    let conn = Connection::open_in_memory().expect("db");
    ensure_schema(&conn).expect("schema");
    conn
}

#[test]
fn upsert_get_list_delete() {
    let conn = open_db();
    let lim = upsert_limit(
        &conn,
        &json!({"name": "database", "limit": 2, "now": "2020-01-01T00:00:00Z"}),
    )
    .unwrap();
    assert_eq!(lim["name"], "database");
    assert_eq!(lim["limit"], 2);

    let listed = list_limits(&conn).unwrap();
    assert_eq!(listed["limits"].as_array().unwrap().len(), 1);

    delete_limit(&conn, "database").unwrap();
    let gone = get_limit(&conn, "database").unwrap();
    assert!(gone["limit"].is_null());
}

#[test]
fn acquire_respects_limit_and_releases() {
    let conn = open_db();
    upsert_limit(
        &conn,
        &json!({"name": "db", "limit": 2, "now": "2020-01-01T00:00:00Z"}),
    )
    .unwrap();

    let a1 = acquire(
        &conn,
        &json!({
            "names": ["db"], "occupy": 1, "now": "2020-01-01T00:00:00Z",
            "lease_duration": 60
        }),
    )
    .unwrap();
    assert_eq!(a1["status"], "acquired");
    let a2 = acquire(
        &conn,
        &json!({
            "names": ["db"], "occupy": 1, "now": "2020-01-01T00:00:00Z",
            "lease_duration": 60
        }),
    )
    .unwrap();
    assert_eq!(a2["status"], "acquired");
    let blocked = acquire(
        &conn,
        &json!({
            "names": ["db"], "occupy": 1, "now": "2020-01-01T00:00:00Z",
            "lease_duration": 60
        }),
    )
    .unwrap();
    assert_eq!(blocked["status"], "would_block");

    let lids = a1["lease_ids"].clone();
    release(
        &conn,
        &json!({"lease_ids": lids, "now": "2020-01-01T00:00:01Z"}),
    )
    .unwrap();
    let a3 = acquire(
        &conn,
        &json!({
            "names": ["db"], "occupy": 1, "now": "2020-01-01T00:00:01Z",
            "lease_duration": 60
        }),
    )
    .unwrap();
    assert_eq!(a3["status"], "acquired");
}

#[test]
fn multi_name_acquire_is_all_or_nothing() {
    let conn = open_db();
    upsert_limit(
        &conn,
        &json!({"name": "a", "limit": 1, "now": "2020-01-01T00:00:00Z"}),
    )
    .unwrap();
    upsert_limit(
        &conn,
        &json!({"name": "b", "limit": 1, "now": "2020-01-01T00:00:00Z"}),
    )
    .unwrap();
    // Fill b
    acquire(
        &conn,
        &json!({"names": ["b"], "now": "2020-01-01T00:00:00Z", "lease_duration": 60}),
    )
    .unwrap();
    let out = acquire(
        &conn,
        &json!({
            "names": ["a", "b"],
            "now": "2020-01-01T00:00:00Z",
            "lease_duration": 60
        }),
    )
    .unwrap();
    assert_eq!(out["status"], "would_block");
    // a must still be free
    let a = acquire(
        &conn,
        &json!({"names": ["a"], "now": "2020-01-01T00:00:00Z", "lease_duration": 60}),
    )
    .unwrap();
    assert_eq!(a["status"], "acquired");
}

#[test]
fn missing_limit_bypasses_unless_strict() {
    let conn = open_db();
    let soft = acquire(
        &conn,
        &json!({"names": ["missing"], "now": "2020-01-01T00:00:00Z"}),
    )
    .unwrap();
    assert_eq!(soft["status"], "bypassed");
    let hard = acquire(
        &conn,
        &json!({"names": ["missing"], "strict": true, "now": "2020-01-01T00:00:00Z"}),
    )
    .unwrap();
    assert_eq!(hard["ok"], false);
    assert_eq!(hard["status"], "missing");
}

#[test]
fn limit_zero_denies() {
    let conn = open_db();
    upsert_limit(
        &conn,
        &json!({"name": "tag:db", "limit": 0, "now": "2020-01-01T00:00:00Z"}),
    )
    .unwrap();
    let out = acquire(
        &conn,
        &json!({"names": ["tag:db"], "now": "2020-01-01T00:00:00Z"}),
    )
    .unwrap();
    assert_eq!(out["status"], "denied");
}

#[test]
fn reclaim_expired_frees_slots() {
    let conn = open_db();
    upsert_limit(
        &conn,
        &json!({"name": "db", "limit": 1, "now": "2020-01-01T00:00:00Z"}),
    )
    .unwrap();
    acquire(
        &conn,
        &json!({
            "names": ["db"],
            "now": "2020-01-01T00:00:00Z",
            "lease_duration": 1
        }),
    )
    .unwrap();
    let blocked = acquire(
        &conn,
        &json!({
            "names": ["db"],
            "now": "2020-01-01T00:00:00.500Z",
            "lease_duration": 1
        }),
    )
    .unwrap();
    assert_eq!(blocked["status"], "would_block");
    let free = acquire(
        &conn,
        &json!({
            "names": ["db"],
            "now": "2020-01-01T00:00:02Z",
            "lease_duration": 1
        }),
    )
    .unwrap();
    assert_eq!(free["status"], "acquired");
}

#[test]
fn rate_limit_requires_decay_and_expires() {
    let conn = open_db();
    upsert_limit(
        &conn,
        &json!({"name": "api", "limit": 1, "now": "2020-01-01T00:00:00Z"}),
    )
    .unwrap();
    let no_decay = acquire(
        &conn,
        &json!({
            "names": ["api"],
            "mode": "rate_limit",
            "now": "2020-01-01T00:00:00Z"
        }),
    )
    .unwrap();
    assert_eq!(no_decay["status"], "decay_required");

    upsert_limit(
        &conn,
        &json!({
            "name": "api",
            "limit": 1,
            "slot_decay_per_second": 1.0,
            "now": "2020-01-01T00:00:00Z"
        }),
    )
    .unwrap();
    let a1 = acquire(
        &conn,
        &json!({
            "names": ["api"],
            "mode": "rate_limit",
            "occupy": 1,
            "now": "2020-01-01T00:00:00Z"
        }),
    )
    .unwrap();
    assert_eq!(a1["status"], "acquired");
    let blocked = acquire(
        &conn,
        &json!({
            "names": ["api"],
            "mode": "rate_limit",
            "now": "2020-01-01T00:00:00.100Z"
        }),
    )
    .unwrap();
    assert_eq!(blocked["status"], "would_block");
    let later = acquire(
        &conn,
        &json!({
            "names": ["api"],
            "mode": "rate_limit",
            "now": "2020-01-01T00:00:01.100Z"
        }),
    )
    .unwrap();
    assert_eq!(later["status"], "acquired");
}

#[test]
fn release_is_idempotent() {
    let conn = open_db();
    upsert_limit(
        &conn,
        &json!({"name": "db", "limit": 1, "now": "2020-01-01T00:00:00Z"}),
    )
    .unwrap();
    let a = acquire(
        &conn,
        &json!({"names": ["db"], "now": "2020-01-01T00:00:00Z", "lease_duration": 60}),
    )
    .unwrap();
    let lids = a["lease_ids"].clone();
    let r1 = release(
        &conn,
        &json!({"lease_ids": lids.clone(), "now": "2020-01-01T00:00:01Z"}),
    )
    .unwrap();
    assert_eq!(r1["released"], 1);
    let r2 = release(
        &conn,
        &json!({"lease_ids": lids, "now": "2020-01-01T00:00:02Z"}),
    )
    .unwrap();
    assert_eq!(r2["released"], 0);
}

#[test]
fn release_by_holders_frees_slots() {
    let conn = open_db();
    upsert_limit(
        &conn,
        &json!({"name": "db", "limit": 1, "now": "2020-01-01T00:00:00Z"}),
    )
    .unwrap();
    let a = acquire(
        &conn,
        &json!({
            "names": ["db"],
            "now": "2020-01-01T00:00:00Z",
            "lease_duration": 60,
            "holder_type": "task_run",
            "holder_id": "task-1"
        }),
    )
    .unwrap();
    assert_eq!(a["status"], "acquired");
    let blocked = acquire(
        &conn,
        &json!({"names": ["db"], "now": "2020-01-01T00:00:00Z", "lease_duration": 60}),
    )
    .unwrap();
    assert_eq!(blocked["status"], "would_block");
    let out = release_by_holders(
        &conn,
        &json!({"holder_ids": ["task-1"], "now": "2020-01-01T00:00:01Z"}),
    )
    .unwrap();
    assert_eq!(out["released"], 1);
    let again = acquire(
        &conn,
        &json!({"names": ["db"], "now": "2020-01-01T00:00:02Z", "lease_duration": 60}),
    )
    .unwrap();
    assert_eq!(again["status"], "acquired");
    let empty = release_by_holders(&conn, &json!({"holder_ids": []})).unwrap();
    assert_eq!(empty["released"], 0);
}

#[test]
fn tag_limit_name_helper() {
    assert_eq!(tag_limit_name("db"), "tag:db");
}
