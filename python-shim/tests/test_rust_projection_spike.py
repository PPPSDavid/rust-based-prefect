"""Functional parity for optional Rust-backed `task_runs` projection UPDATE (spike)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from prefect_compat.rust_bridge import load_ironflow_library, try_rust_projection_update_task_run


def _init_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE task_runs (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT UNIQUE NOT NULL,
            flow_run_id TEXT NOT NULL,
            task_name TEXT NOT NULL,
            planned_node_id TEXT,
            state TEXT NOT NULL,
            version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        "INSERT INTO task_runs(id,flow_run_id,task_name,planned_node_id,state,version,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("tid", "fid", "t", None, "PENDING", 1, "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()


def _rust_symbol_available() -> bool:
    try:
        lib = load_ironflow_library()
    except RuntimeError:
        return False
    return getattr(lib, "ironflow_projection_update_task_run", None) is not None


def test_projection_task_update_matches_python_sqlite(tmp_path: Path) -> None:
    ts = "2026-05-11T12:00:00+00:00"
    db_py = tmp_path / "py.db"
    db_rs = tmp_path / "rs.db"
    _init_db(db_py)
    _init_db(db_rs)

    conn = sqlite3.connect(db_py)
    conn.execute(
        "UPDATE task_runs SET state = ?, version = ?, updated_at = ? WHERE id = ?",
        ("RUNNING", 2, ts, "tid"),
    )
    conn.commit()
    conn.close()

    os.environ["IRONFLOW_RUST_PROJECTION"] = "1"
    try:
        if not _rust_symbol_available():
            pytest.skip("ironflow_projection_update_task_run not in native library")
        ok = try_rust_projection_update_task_run(str(db_rs), "tid", "RUNNING", 2, ts)
        assert ok is True
    finally:
        os.environ.pop("IRONFLOW_RUST_PROJECTION", None)

    def read_row(path: Path) -> tuple:
        c = sqlite3.connect(path)
        row = c.execute("SELECT state, version, updated_at FROM task_runs WHERE id=?", ("tid",)).fetchone()
        c.close()
        return row

    assert read_row(db_py) == read_row(db_rs)


def test_projection_disabled_uses_python_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ts = "2026-05-11T12:00:00+00:00"
    db_path = tmp_path / "only.db"
    _init_db(db_path)
    monkeypatch.delenv("IRONFLOW_RUST_PROJECTION", raising=False)
    assert try_rust_projection_update_task_run(str(db_path), "tid", "RUNNING", 2, ts) is False
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT state, version FROM task_runs WHERE id=?", ("tid",)).fetchone()
    conn.close()
    assert row == ("PENDING", 1)
