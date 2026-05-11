#!/usr/bin/env python3
"""Micro-benchmark: Python sqlite UPDATE vs Rust FFI for `task_runs` projection (spike)."""

from __future__ import annotations

import os
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_SHIM_SRC = ROOT / "python-shim" / "src"
if str(PYTHON_SHIM_SRC) not in sys.path:
    sys.path.insert(0, str(PYTHON_SHIM_SRC))

from prefect_compat.rust_bridge import load_ironflow_library, try_rust_projection_update_task_run


def _percentile(sorted_ms: list[float], p: float) -> float:
    if not sorted_ms:
        return 0.0
    k = (len(sorted_ms) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_ms) - 1)
    return sorted_ms[f] + (k - f) * (sorted_ms[c] - sorted_ms[f])


def _bench_python(conn: sqlite3.Connection, n: int, ts_fn) -> list[float]:
    times: list[float] = []
    for i in range(n):
        ts = ts_fn(i)
        t0 = time.perf_counter()
        conn.execute(
            "UPDATE task_runs SET state = ?, version = ?, updated_at = ? WHERE id = ?",
            ("RUNNING", 2 + i, ts, "tid"),
        )
        conn.commit()
        times.append((time.perf_counter() - t0) * 1000.0)
    return times


def _bench_rust(db_path: Path, n: int, ts_fn) -> list[float]:
    os.environ["IRONFLOW_RUST_PROJECTION"] = "1"
    times: list[float] = []
    try:
        try:
            lib = load_ironflow_library()
        except RuntimeError:
            return []
        if getattr(lib, "ironflow_projection_update_task_run", None) is None:
            return []
        for i in range(n):
            ts = ts_fn(i)
            t0 = time.perf_counter()
            ok = try_rust_projection_update_task_run(str(db_path), "tid", "RUNNING", 2 + i, ts)
            if not ok:
                print(
                    "Rust path returned False during benchmark (library or FFI issue).",
                    file=sys.stderr,
                )
                return []
            times.append((time.perf_counter() - t0) * 1000.0)
    finally:
        os.environ.pop("IRONFLOW_RUST_PROJECTION", None)
    return times


def main() -> int:
    n = 2000
    with tempfile.TemporaryDirectory() as td:
        db_py = Path(td) / "bench_py.db"
        db_rs = Path(td) / "bench_rs.db"
        for p in (db_py, db_rs):
            conn = sqlite3.connect(p)
            conn.execute(
                """CREATE TABLE task_runs (
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

        def ts_fn(i: int) -> str:
            return f"2026-05-11T12:00:{i % 60:02d}.000000+00:00"

        conn_py = sqlite3.connect(db_py)
        py_times = _bench_python(conn_py, n, ts_fn)
        conn_py.close()

        rs_times = _bench_rust(db_rs, n, ts_fn)

        py_sorted = sorted(py_times)
        summary_py = (
            statistics.median(py_sorted),
            _percentile(py_sorted, 0.95),
        )
        print(f"Python sqlite UPDATE x{n}: median={summary_py[0]:.4f} ms  p95={summary_py[1]:.4f} ms")
        if rs_times:
            rs_sorted = sorted(rs_times)
            summary_rs = (
                statistics.median(rs_sorted),
                _percentile(rs_sorted, 0.95),
            )
            print(f"Rust FFI UPDATE x{n}: median={summary_rs[0]:.4f} ms  p95={summary_rs[1]:.4f} ms")
            speedup = summary_py[0] / summary_rs[0] if summary_rs[0] > 0 else 0.0
            print(f"Median speedup (Python / Rust): {speedup:.2f}x")
        else:
            print("Rust timings skipped (library missing or FFI unavailable).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
