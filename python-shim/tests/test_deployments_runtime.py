"""Deployment queue semantics (concurrency, leases, schedules) — direct control plane, no HTTP worker threads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import control_plane


def _swap_plane(tmp_path: Path) -> None:
    history = tmp_path / "deployments-history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    control_plane._flows = plane._flows
    control_plane._tasks = plane._tasks
    control_plane._events = plane._events
    control_plane._tokens = plane._tokens
    control_plane._history_path = plane._history_path
    control_plane._sqlite_path = plane._sqlite_path
    control_plane._sqlite_conn = plane._sqlite_conn
    control_plane._manifest_by_task = plane._manifest_by_task
    control_plane._rust_bridge = plane._rust_bridge
    control_plane._rust_fsm_bridge = plane._rust_fsm_bridge
    control_plane._rust_fsm_handle = plane._rust_fsm_handle
    control_plane._rust_native_persistence = plane._rust_native_persistence
    control_plane._test_plane_ref = plane
    set_control_plane(control_plane)


def _set_dep_policy(
    deployment_id: str,
    *,
    concurrency_limit: int | None,
    collision_strategy: str,
) -> None:
    control_plane._sqlite_conn.execute(
        "UPDATE deployments SET concurrency_limit = ?, collision_strategy = ? WHERE id = ?",
        (concurrency_limit, collision_strategy, deployment_id),
    )


def test_cancel_new_when_at_exec_capacity(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    dep = control_plane.create_deployment(
        name="cnc-flow",
        flow_name="simple_flow",
        default_parameters={"n": 1},
        paused=False,
    )
    _set_dep_policy(dep["id"], concurrency_limit=1, collision_strategy="CANCEL_NEW")
    d_id = UUID(dep["id"])

    first = control_plane.trigger_deployment_run(d_id, parameters={})
    assert first["status"] == "SCHEDULED"
    claimed = control_plane.claim_next_deployment_run(worker_name="w1", lease_seconds=30)
    assert claimed is not None
    assert claimed["id"] == first["id"]

    second = control_plane.trigger_deployment_run(d_id, parameters={})
    assert second["status"] == "CANCELLED"
    assert second.get("error") == "concurrency limit reached"


def test_enqueue_second_run_stays_scheduled_until_slot_frees(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    dep = control_plane.create_deployment(
        name="enq-flow",
        flow_name="simple_flow",
        default_parameters={"n": 1},
        paused=False,
    )
    _set_dep_policy(dep["id"], concurrency_limit=1, collision_strategy="ENQUEUE")
    d_id = UUID(dep["id"])

    r1 = control_plane.trigger_deployment_run(d_id, parameters={})
    r2 = control_plane.trigger_deployment_run(d_id, parameters={})
    assert r1["status"] == "SCHEDULED"
    assert r2["status"] == "SCHEDULED"

    c1 = control_plane.claim_next_deployment_run(worker_name="w1", lease_seconds=30)
    assert c1 is not None
    c2 = control_plane.claim_next_deployment_run(worker_name="w1", lease_seconds=30)
    assert c2 is None

    control_plane.mark_deployment_run_finished(UUID(c1["id"]), "COMPLETED", flow_run_id=None)
    c3 = control_plane.claim_next_deployment_run(worker_name="w1", lease_seconds=30)
    assert c3 is not None
    assert c3["id"] == r2["id"]


def test_reclaim_expired_claim_back_to_scheduled(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    dep = control_plane.create_deployment(
        name="lease-flow",
        flow_name="simple_flow",
        default_parameters={"n": 1},
        paused=False,
    )
    d_id = UUID(dep["id"])
    run = control_plane.trigger_deployment_run(d_id, parameters={})
    claimed = control_plane.claim_next_deployment_run(worker_name="w1", lease_seconds=30)
    assert claimed is not None

    past = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    control_plane._sqlite_conn.execute(
        "UPDATE deployment_runs SET lease_until = ? WHERE id = ?",
        (past, run["id"]),
    )
    reclaimed = control_plane._reclaim_expired_claims_python()
    assert reclaimed >= 1
    row = control_plane._query_rows(
        "SELECT status FROM deployment_runs WHERE id = ?",
        [run["id"]],
    )
    assert row[0]["status"] == "SCHEDULED"


def test_interval_schedule_tick_inserts_run(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    dep = control_plane.create_deployment(
        name="sched-flow",
        flow_name="simple_flow",
        default_parameters={"n": 1},
        paused=False,
    )
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    ts = datetime.now(UTC).isoformat()
    control_plane._sqlite_conn.execute(
        """
        UPDATE deployments
        SET schedule_enabled = 1,
            schedule_interval_seconds = 3600,
            schedule_next_run_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (past, ts, dep["id"]),
    )
    before = control_plane._query_rows(
        "SELECT COUNT(*) AS c FROM deployment_runs WHERE deployment_id = ?",
        [dep["id"]],
    )[0]["c"]
    n = control_plane._tick_deployment_schedules_python()
    assert n == 1
    after = control_plane._query_rows(
        "SELECT COUNT(*) AS c FROM deployment_runs WHERE deployment_id = ?",
        [dep["id"]],
    )[0]["c"]
    assert after == before + 1


def test_deployment_maintenance_prefers_rust_when_bound(tmp_path: Path) -> None:
    """Smoke: maintenance tick runs without error; Rust path is used when FSM + DB are bound."""
    _swap_plane(tmp_path)
    summary = control_plane.deployment_maintenance_tick(stale_after_seconds=120)
    assert "reclaimed" in summary and "triggered" in summary and "reaped" in summary
    if control_plane._rust_fsm_active() and getattr(control_plane, "_rust_db_bound", False):
        # Single FFI op returns all three counters when native build matches.
        assert isinstance(summary["reclaimed"], int)


@pytest.mark.parametrize("worker", ["rust-worker", "py-worker"])
def test_worker_heartbeat_upsert(tmp_path: Path, worker: str) -> None:
    _swap_plane(tmp_path)
    control_plane.worker_heartbeat(worker)
    rows = control_plane._query_rows("SELECT name, status FROM workers WHERE name = ?", [worker])
    assert rows and rows[0]["status"] == "ONLINE"
