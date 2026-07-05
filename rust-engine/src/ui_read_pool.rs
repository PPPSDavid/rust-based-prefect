//! Thread-local SQLite read connection pool for ``ironflow_query``.
//!
//! Each thread reuses one ``Connection`` per database path instead of opening a new
//! handle on every query. Safe under WAL: readers do not block writers on other
//! connections; writers use the bound FSM connection on the write path.

use std::cell::RefCell;
use std::collections::HashMap;
use std::time::Duration;

use rusqlite::Connection;

thread_local! {
    static READ_CONN_POOL: RefCell<HashMap<String, Connection>> = RefCell::new(HashMap::new());
}

/// Run ``f`` with a thread-local read connection for ``db_path`` (opened on first use).
pub fn with_read_connection<F, T>(db_path: &str, f: F) -> Result<T, String>
where
    F: FnOnce(&Connection) -> Result<T, String>,
{
    READ_CONN_POOL.with(|pool| {
        let mut map = pool.borrow_mut();
        if !map.contains_key(db_path) {
            let conn = Connection::open(db_path).map_err(|e| e.to_string())?;
            conn.busy_timeout(Duration::from_millis(5_000))
                .map_err(|e| e.to_string())?;
            map.insert(db_path.to_string(), conn);
        }
        let conn = map
            .get(db_path)
            .ok_or_else(|| "read connection pool missing entry".to_string())?;
        f(conn)
    })
}
