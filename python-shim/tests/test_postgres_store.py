"""Tier B1 Postgres store + dialect adapter tests (requires FLOWOXIDE_DATABASE_URL)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from prefect_compat.persistence import (
    DEFAULT_WORK_POOL_ID,
    PostgresStore,
    create_store,
)
from prefect_compat.persistence.dialect import rewrite_sqlite_sql_for_postgres
from prefect_compat.runtime import InMemoryControlPlane

PG_URL = os.getenv(
    "FLOWOXIDE_DATABASE_URL",
    "postgresql://flowoxide:flowoxide@127.0.0.1:5432/flowoxide_b1",
)


def _pg_available() -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(PG_URL, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="Postgres not available (set FLOWOXIDE_DATABASE_URL / start Postgres)",
)


def test_rewrite_placeholders_and_ignore() -> None:
    sql = "INSERT OR IGNORE INTO flow_runs(id) VALUES(?)"
    out = rewrite_sqlite_sql_for_postgres(sql)
    assert "%s" in out
    assert "ON CONFLICT DO NOTHING" in out
    assert "?" not in out


def test_rewrite_rowid_to_seq() -> None:
    sql = "SELECT rowid AS seq FROM work_pools WHERE rowid < ? ORDER BY rowid DESC LIMIT ?"
    out = rewrite_sqlite_sql_for_postgres(sql)
    assert "seq AS seq" in out or "seq" in out
    assert "rowid" not in out.lower()


def test_postgres_store_schema_and_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWOXIDE_DATABASE_URL", PG_URL)
    # unique history path so JSONL sidecar does not collide; schema is shared DB
    history = Path(f"/tmp/flowoxide-b1-{uuid.uuid4().hex}.jsonl")
    store = create_store(history_path=history)
    assert isinstance(store, PostgresStore)
    assert store.backend_kind == "postgres"
    assert store.path is None
    store.close()

    plane = InMemoryControlPlane(history_path=str(history))
    assert plane._store.backend_kind == "postgres"
    # schema seeded default pool
    row = plane._sqlite_conn.execute(
        "SELECT id FROM work_pools WHERE id = ?", [DEFAULT_WORK_POOL_ID]
    ).fetchone()
    assert row is not None
    assert row["id"] == DEFAULT_WORK_POOL_ID

    pool_id = f"pool-{uuid.uuid4().hex[:10]}"
    plane._sqlite_conn.execute(
        """
        INSERT INTO work_pools(id,name,type,paused,created_at,updated_at)
        VALUES(?,?,?,?,?,?)
        """,
        [pool_id, pool_id, "process", 0, plane._now(), plane._now()],
    )

    # Create deployment + scheduled run via Python paths, claim via API
    dep = plane.create_deployment(
        name=f"b1-dep-{uuid.uuid4().hex[:8]}",
        flow_name="echo",
        entrypoint=None,
        path=None,
        default_parameters={},
        paused=False,
        concurrency_limit=None,
        collision_strategy="ENQUEUE",
        work_pool_id=pool_id,
    )
    run = plane.trigger_deployment_run(dep["id"], parameters={}, idempotency_key=None)
    assert run["status"] == "SCHEDULED"

    claimed = plane.claim_next_deployment_run(
        worker_name=f"w-{uuid.uuid4().hex[:6]}", work_pool_id=pool_id
    )
    assert claimed is not None
    assert claimed["id"] == run["id"]
    assert claimed["status"] == "CLAIMED"
    # Prefer Rust PG bind when native available
    if plane._rust_fsm_active():
        assert plane._rust_db_bound is True
