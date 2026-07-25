"""SQLite control-plane store (default / local-dev backend)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..concurrency_store import ensure_schema as ensure_gcl_schema
from .constants import DEFAULT_WORK_POOL_ID


class SqliteStore:
    """File-backed SQLite store with WAL + additive schema upgrades."""

    backend_kind = "sqlite"

    def __init__(self, path: Path, conn: sqlite3.Connection) -> None:
        self._path = path
        self._conn = conn

    @classmethod
    def open(cls, sqlite_path: Path) -> SqliteStore:
        """Open (or recover) a SQLite DB and ensure schema."""
        path = Path(sqlite_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = cls._open_connection(path)
        store = cls(path, conn)
        store.ensure_schema()
        return store

    @property
    def path(self) -> Path:
        return self._path

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def ensure_schema(self) -> None:
        self._init_schema(self._conn)
        self._ensure_schema_upgrades(self._conn)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    @staticmethod
    def _open_connection(sqlite_path: Path) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(sqlite_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.isolation_level = None  # autocommit per statement
            return conn
        except sqlite3.DatabaseError as exc:
            if "malformed" not in str(exc).lower():
                raise
            # Recover local dev/test DBs that got corrupted by abrupt interruption.
            try:
                sqlite_path.unlink(missing_ok=True)
            except Exception:
                pass
            conn = sqlite3.connect(str(sqlite_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.isolation_level = None
            return conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS flow_runs (
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
                resume_from_flow_run_id TEXT,
                resume_lineage_id TEXT
            );
            CREATE TABLE IF NOT EXISTS task_runs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                flow_run_id TEXT NOT NULL,
                task_name TEXT NOT NULL,
                planned_node_id TEXT,
                state TEXT NOT NULL,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'task',
                child_flow_run_id TEXT,
                child_deployment_run_id TEXT,
                tags TEXT,
                contribute_to_flow_state INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS dag_manifests (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                flow_run_id TEXT UNIQUE NOT NULL,
                manifest_json TEXT NOT NULL,
                forecast_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                fallback_required INTEGER NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS logs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                flow_run_id TEXT NOT NULL,
                task_run_id TEXT,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                run_id TEXT NOT NULL,
                task_run_id TEXT,
                from_state TEXT,
                to_state TEXT,
                event_type TEXT,
                kind TEXT,
                data TEXT,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                flow_run_id TEXT NOT NULL,
                task_run_id TEXT,
                artifact_type TEXT NOT NULL,
                key TEXT NOT NULL,
                summary TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deployments (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                name TEXT UNIQUE NOT NULL,
                flow_name TEXT NOT NULL,
                entrypoint TEXT,
                path TEXT,
                default_parameters TEXT NOT NULL,
                paused INTEGER NOT NULL,
                concurrency_limit INTEGER,
                collision_strategy TEXT NOT NULL DEFAULT 'ENQUEUE',
                schedule_interval_seconds INTEGER,
                schedule_cron TEXT,
                schedule_rrule TEXT,
                schedule_next_run_at TEXT,
                schedule_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deployment_runs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                deployment_id TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_parameters TEXT NOT NULL,
                resolved_parameters TEXT NOT NULL,
                idempotency_key TEXT,
                worker_name TEXT,
                lease_until TEXT,
                flow_run_id TEXT,
                error TEXT,
                parent_flow_run_id TEXT,
                parent_task_run_id TEXT,
                parent_deployment_run_id TEXT,
                resume_from_flow_run_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_deployment_runs_idempotency
                ON deployment_runs(deployment_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL;
            CREATE TABLE IF NOT EXISTS workers (
                name TEXT PRIMARY KEY,
                last_heartbeat TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                work_pool_id TEXT
            );
            CREATE TABLE IF NOT EXISTS work_pools (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                type TEXT NOT NULL DEFAULT 'process',
                paused INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_flow_runs_state_created
                ON flow_runs(state, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_task_runs_flow_start
                ON task_runs(flow_run_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_logs_flow_ts
                ON logs(flow_run_id, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_logs_task_ts
                ON logs(task_run_id, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_events_run_ts
                ON events(run_id, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_deployments_name
                ON deployments(name);
            CREATE INDEX IF NOT EXISTS idx_deployment_runs_status_created
                ON deployment_runs(status, created_at ASC);
            CREATE INDEX IF NOT EXISTS idx_deployment_runs_deployment_created
                ON deployment_runs(deployment_id, created_at DESC);
            """
        )

    @staticmethod
    def _ensure_schema_upgrades(conn: sqlite3.Connection) -> None:
        cols = conn.execute("PRAGMA table_info(task_runs)").fetchall()
        col_names = {col["name"] for col in cols}
        if "planned_node_id" not in col_names:
            conn.execute("ALTER TABLE task_runs ADD COLUMN planned_node_id TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_runs_flow_planned ON task_runs(flow_run_id, planned_node_id)"
        )
        dep_cols = {
            c["name"] for c in conn.execute("PRAGMA table_info(deployments)").fetchall()
        }
        if "concurrency_limit" not in dep_cols:
            conn.execute("ALTER TABLE deployments ADD COLUMN concurrency_limit INTEGER")
        if "collision_strategy" not in dep_cols:
            conn.execute(
                "ALTER TABLE deployments ADD COLUMN collision_strategy TEXT NOT NULL DEFAULT 'ENQUEUE'"
            )
        if "schedule_interval_seconds" not in dep_cols:
            conn.execute(
                "ALTER TABLE deployments ADD COLUMN schedule_interval_seconds INTEGER"
            )
        if "schedule_cron" not in dep_cols:
            conn.execute("ALTER TABLE deployments ADD COLUMN schedule_cron TEXT")
        if "schedule_rrule" not in dep_cols:
            conn.execute("ALTER TABLE deployments ADD COLUMN schedule_rrule TEXT")
        if "schedule_next_run_at" not in dep_cols:
            conn.execute("ALTER TABLE deployments ADD COLUMN schedule_next_run_at TEXT")
        if "schedule_enabled" not in dep_cols:
            conn.execute(
                "ALTER TABLE deployments ADD COLUMN schedule_enabled INTEGER NOT NULL DEFAULT 0"
            )
        if "work_pool_id" not in dep_cols:
            conn.execute(
                f"ALTER TABLE deployments ADD COLUMN work_pool_id TEXT DEFAULT '{DEFAULT_WORK_POOL_ID}'"
            )
        worker_cols = {
            c["name"] for c in conn.execute("PRAGMA table_info(workers)").fetchall()
        }
        if "work_pool_id" not in worker_cols:
            conn.execute("ALTER TABLE workers ADD COLUMN work_pool_id TEXT")
        flow_cols = {
            c["name"] for c in conn.execute("PRAGMA table_info(flow_runs)").fetchall()
        }
        if "parent_flow_run_id" not in flow_cols:
            conn.execute("ALTER TABLE flow_runs ADD COLUMN parent_flow_run_id TEXT")
        if "parent_task_run_id" not in flow_cols:
            conn.execute("ALTER TABLE flow_runs ADD COLUMN parent_task_run_id TEXT")
        if "root_flow_run_id" not in flow_cols:
            conn.execute("ALTER TABLE flow_runs ADD COLUMN root_flow_run_id TEXT")
        if "execution_mode" not in flow_cols:
            conn.execute("ALTER TABLE flow_runs ADD COLUMN execution_mode TEXT")
        if "depth" not in flow_cols:
            conn.execute(
                "ALTER TABLE flow_runs ADD COLUMN depth INTEGER NOT NULL DEFAULT 0"
            )
        if "resume_from_flow_run_id" not in flow_cols:
            conn.execute(
                "ALTER TABLE flow_runs ADD COLUMN resume_from_flow_run_id TEXT"
            )
        if "resume_lineage_id" not in flow_cols:
            conn.execute("ALTER TABLE flow_runs ADD COLUMN resume_lineage_id TEXT")
        if "kind" not in col_names:
            conn.execute(
                "ALTER TABLE task_runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'task'"
            )
        if "child_flow_run_id" not in col_names:
            conn.execute("ALTER TABLE task_runs ADD COLUMN child_flow_run_id TEXT")
        if "child_deployment_run_id" not in col_names:
            conn.execute(
                "ALTER TABLE task_runs ADD COLUMN child_deployment_run_id TEXT"
            )
        if "gate_open_at" not in col_names:
            conn.execute("ALTER TABLE task_runs ADD COLUMN gate_open_at TEXT")
        if "tags" not in col_names:
            conn.execute("ALTER TABLE task_runs ADD COLUMN tags TEXT")
        if "contribute_to_flow_state" not in col_names:
            conn.execute(
                "ALTER TABLE task_runs ADD COLUMN contribute_to_flow_state "
                "INTEGER NOT NULL DEFAULT 1"
            )
        # Global / tag concurrency limit tables (Python fallback + shared schema).
        ensure_gcl_schema(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_runs_gate_due "
            "ON task_runs(kind, state, gate_open_at)"
        )
        dep_run_cols = {
            c["name"]
            for c in conn.execute("PRAGMA table_info(deployment_runs)").fetchall()
        }
        if "parent_flow_run_id" not in dep_run_cols:
            conn.execute(
                "ALTER TABLE deployment_runs ADD COLUMN parent_flow_run_id TEXT"
            )
        if "parent_task_run_id" not in dep_run_cols:
            conn.execute(
                "ALTER TABLE deployment_runs ADD COLUMN parent_task_run_id TEXT"
            )
        if "parent_deployment_run_id" not in dep_run_cols:
            conn.execute(
                "ALTER TABLE deployment_runs ADD COLUMN parent_deployment_run_id TEXT"
            )
        if "resume_from_flow_run_id" not in dep_run_cols:
            conn.execute(
                "ALTER TABLE deployment_runs ADD COLUMN resume_from_flow_run_id TEXT"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_deployments_work_pool ON deployments(work_pool_id)"
        )
