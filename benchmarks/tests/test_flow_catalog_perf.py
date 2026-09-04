"""Wall-clock bound for catalog list/create so the feature stays off the hot Python loop."""

from __future__ import annotations

import time
from pathlib import Path

from prefect_compat.runtime import InMemoryControlPlane

# Generous CI bound: Rust JOIN + GROUP BY for a few hundred identities.
_LIST_BUDGET_SECONDS = 1.5
_CREATE_BUDGET_SECONDS = 8.0
_N = 300


def test_catalog_create_and_list_bound(tmp_path: Path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "catalog-perf.jsonl"))
    t0 = time.perf_counter()
    for i in range(_N):
        plane.ensure_flow(f"perf-flow-{i:04d}")
    create_elapsed = time.perf_counter() - t0
    assert create_elapsed < _CREATE_BUDGET_SECONDS, create_elapsed

    t1 = time.perf_counter()
    page = plane.list_flows(limit=200, status="active")
    list_elapsed = time.perf_counter() - t1
    assert len(page.items) == 200
    assert list_elapsed < _LIST_BUDGET_SECONDS, list_elapsed

    t2 = time.perf_counter()
    runs_page = plane.list_flow_runs(limit=50)
    runs_elapsed = time.perf_counter() - t2
    assert runs_page.items is not None
    assert runs_elapsed < _LIST_BUDGET_SECONDS, runs_elapsed
