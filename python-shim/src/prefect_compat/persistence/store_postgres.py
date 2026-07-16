"""Postgres control-plane store (production / compose backend)."""

from __future__ import annotations

from typing import Any

from .constants import DEFAULT_WORK_POOL_ID
from .dialect import PostgresConnectionAdapter


_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS flow_runs (
    seq BIGSERIAL PRIMARY KEY,
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
    depth INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS task_runs (
    seq BIGSERIAL PRIMARY KEY,
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
    gate_open_at TEXT,
    contribute_to_flow_state INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS dag_manifests (
    seq BIGSERIAL PRIMARY KEY,
    flow_run_id TEXT UNIQUE NOT NULL,
    manifest_json TEXT NOT NULL,
    forecast_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    fallback_required INTEGER NOT NULL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS logs (
    seq BIGSERIAL PRIMARY KEY,
    id TEXT UNIQUE NOT NULL,
    flow_run_id TEXT NOT NULL,
    task_run_id TEXT,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq BIGSERIAL PRIMARY KEY,
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
    seq BIGSERIAL PRIMARY KEY,
    id TEXT UNIQUE NOT NULL,
    flow_run_id TEXT NOT NULL,
    task_run_id TEXT,
    artifact_type TEXT NOT NULL,
    key TEXT NOT NULL,
    summary TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deployments (
    seq BIGSERIAL PRIMARY KEY,
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
    work_pool_id TEXT DEFAULT '{DEFAULT_WORK_POOL_ID}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deployment_runs (
    seq BIGSERIAL PRIMARY KEY,
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
    work_pool_id TEXT,
    seq BIGSERIAL
);
CREATE TABLE IF NOT EXISTS work_pools (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL DEFAULT 'process',
    paused INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    seq BIGSERIAL
);
CREATE INDEX IF NOT EXISTS idx_flow_runs_state_created
    ON flow_runs(state, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_runs_flow_start
    ON task_runs(flow_run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_runs_flow_planned
    ON task_runs(flow_run_id, planned_node_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_gate_due
    ON task_runs(kind, state, gate_open_at);
CREATE INDEX IF NOT EXISTS idx_logs_flow_ts
    ON logs(flow_run_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_logs_task_ts
    ON logs(task_run_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_run_ts
    ON events(run_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_deployments_name
    ON deployments(name);
CREATE INDEX IF NOT EXISTS idx_deployments_work_pool
    ON deployments(work_pool_id);
CREATE INDEX IF NOT EXISTS idx_deployment_runs_status_created
    ON deployment_runs(status, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_deployment_runs_deployment_created
    ON deployment_runs(deployment_id, created_at DESC);
"""


class PostgresStore:
    """Network Postgres store with sync psycopg + sqlite-shaped execute adapter."""

    backend_kind = "postgres"

    def __init__(self, database_url: str, conn: Any, adapter: PostgresConnectionAdapter) -> None:
        self._database_url = database_url
        self._raw = conn
        self._adapter = adapter

    @classmethod
    def open(cls, database_url: str) -> PostgresStore:
        # Keep psycopg optional so SQLite default / wheel smoke / perf jobs import
        # prefect_compat without installing the Postgres driver.
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise ImportError(
                "Postgres backend requires psycopg. "
                "Install with: pip install 'psycopg[binary]' "
                "(or ironflow-prefect-compat[postgres])."
            ) from exc
        # dict_row yields dict rows; psycopg's generic Row typing is overly narrow for ty.
        raw = psycopg.connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,  # ty: ignore[invalid-argument-type]
        )
        adapter = PostgresConnectionAdapter(raw)
        store = cls(database_url, raw, adapter)
        store.ensure_schema()
        return store

    @property
    def path(self) -> None:
        return None

    @property
    def database_url(self) -> str:
        return self._database_url

    @property
    def connection(self) -> PostgresConnectionAdapter:
        return self._adapter

    def ensure_schema(self) -> None:
        with self._raw.cursor() as cur:
            cur.execute(_SCHEMA_SQL)

    def close(self) -> None:
        try:
            self._adapter.close()
        except Exception:
            pass
