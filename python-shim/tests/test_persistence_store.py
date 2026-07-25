"""Tests for Tier B0 persistence extract (SQLite store + factory)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prefect_compat.persistence import (
    DEFAULT_WORK_POOL_ID,
    SqliteStore,
    create_store,
    resolve_sqlite_path,
)
from prefect_compat.runtime import InMemoryControlPlane


def test_resolve_sqlite_path_from_history() -> None:
    assert resolve_sqlite_path(Path("data/flowoxide_history.jsonl")) == Path(
        "data/flowoxide_history.db"
    )
    assert resolve_sqlite_path(None) == Path("data") / "flowoxide_ui.db"


def test_sqlite_store_open_creates_tables(tmp_path: Path) -> None:
    db = tmp_path / "ui.db"
    store = SqliteStore.open(db)
    try:
        rows = store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {row["name"] for row in rows}
        assert "flow_runs" in names
        assert "deployment_runs" in names
        assert "work_pools" in names
        assert store.backend_kind == "sqlite"
        assert store.path == db
    finally:
        store.close()


def test_sqlite_store_upgrade_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "ui.db"
    store = SqliteStore.open(db)
    store.ensure_schema()
    store.ensure_schema()
    cols = {
        c["name"]
        for c in store.connection.execute("PRAGMA table_info(deployments)").fetchall()
    }
    assert "work_pool_id" in cols
    store.close()


def test_create_store_sqlite_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOWOXIDE_DATABASE_URL", raising=False)
    history = tmp_path / "hist.jsonl"
    store = create_store(history_path=history)
    assert isinstance(store, SqliteStore)
    assert store.path == history.with_suffix(".db")
    store.close()


def test_create_store_postgres_when_url_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    url = os.getenv(
        "FLOWOXIDE_TEST_DATABASE_URL",
        "postgresql://flowoxide:flowoxide@127.0.0.1:5432/flowoxide_b1",
    )
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
    except Exception:
        pytest.skip("Postgres not available")  # ty: ignore[too-many-positional-arguments]

    monkeypatch.setenv("FLOWOXIDE_DATABASE_URL", url)
    from prefect_compat.persistence import PostgresStore

    store = create_store(history_path=None)
    assert isinstance(store, PostgresStore)
    store.close()


def test_control_plane_uses_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FLOWOXIDE_DATABASE_URL", raising=False)
    history = tmp_path / "hist.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    assert plane._store.backend_kind == "sqlite"
    assert plane._sqlite_path == history.with_suffix(".db")
    assert plane._sqlite_conn is plane._store.connection
    # default work pool still seeded
    row = plane._sqlite_conn.execute(
        "SELECT id FROM work_pools WHERE id = ?", [DEFAULT_WORK_POOL_ID]
    ).fetchone()
    assert row is not None
