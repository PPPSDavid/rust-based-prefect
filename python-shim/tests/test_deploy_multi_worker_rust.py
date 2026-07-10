"""Multi-worker deployment runs with Rust bind_db (regression for subflow perf)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from prefect_compat import InMemoryControlPlane, deployment_ref, flow, set_control_plane
from prefect_compat.runtime import InMemoryControlPlane as Plane
from prefect_compat.worker import run_worker_loop

try:
    from prefect_compat.runtime import RustFsmBridge
except ImportError:
    RustFsmBridge = None  # type: ignore[misc, assignment]

pytestmark = pytest.mark.skipif(RustFsmBridge is None, reason="Rust FSM bridge not available")


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / "multi-worker.jsonl"))


def _start_workers(
    plane: InMemoryControlPlane,
    registry: dict,
    *,
    count: int,
    pool_id: str = "default-process-pool",
) -> tuple[threading.Event, list[threading.Thread]]:
    stop = threading.Event()
    threads: list[threading.Thread] = []
    for idx in range(count):
        thread = threading.Thread(
            target=run_worker_loop,
            kwargs={
                "control_plane": plane,
                "worker_name": f"mw-worker-{idx}",
                "work_pool_id": pool_id,
                "flow_registry": registry,
                "lease_seconds": 60,
                "stop_event": stop,
            },
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    return stop, threads


def test_multi_worker_recursive_deploy_chain_rust_bound(tmp_path: Path) -> None:
    """Three workers + depth-3 recursive deploy chain must not corrupt SQLite."""
    plane = _plane(tmp_path)
    if not plane._rust_db_bound:
        pytest.skip("Rust bind_db not active in this build")

    set_control_plane(plane)
    registry: dict = {}
    child_name = "mw-child-deploy"

    @flow
    def chain_child(k: int = 0) -> int:
        if k <= 0:
            return 1
        return deployment_ref(child_name).submit(k=k - 1).result() + 1

    @flow
    def parent_flow() -> int:
        return deployment_ref(child_name).submit(k=2).result()

    registry["chain_child"] = chain_child
    registry["parent_flow"] = parent_flow

    plane.create_deployment(
        name=child_name,
        flow_name="chain_child",
        default_parameters={},
        paused=False,
    )

    stop, workers = _start_workers(plane, registry, count=3)
    try:
        for _ in range(3):
            assert parent_flow() == 3
    finally:
        stop.set()
        for t in workers:
            t.join(timeout=20)

    dep_rows = plane._query_rows(
        "SELECT status, requested_parameters, resolved_parameters FROM deployment_runs",
        [],
    )
    assert len(dep_rows) >= 9
    for row in dep_rows:
        assert str(row["status"]) in {"COMPLETED", "SCHEDULED", "CLAIMED", "RUNNING", "CANCELLED"}
        for col in ("requested_parameters", "resolved_parameters"):
            val = row[col]
            if val is not None and str(val).strip():
                assert str(val).startswith("{")


def test_worker_heartbeat_uses_rust_only_when_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plane = _plane(tmp_path)
    if not plane._rust_db_bound:
        pytest.skip("Rust bind_db not active in this build")

    calls: list[str] = []
    orig_dispatch = Plane._rust_deployment_dispatch

    def spy(self: Plane, op: str, body: dict) -> dict | None:
        if op == "deployment_worker_heartbeat":
            calls.append(op)
        return orig_dispatch(self, op, body)

    monkeypatch.setattr(Plane, "_rust_deployment_dispatch", spy)
    plane.worker_heartbeat("mw-heartbeat-worker", work_pool_id="default-process-pool")
    assert calls == ["deployment_worker_heartbeat"]

    rows = plane._query_rows(
        "SELECT name FROM workers WHERE name = ?",
        ["mw-heartbeat-worker"],
    )
    assert len(rows) == 1
