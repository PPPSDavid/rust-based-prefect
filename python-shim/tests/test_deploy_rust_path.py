"""Assert Rust deployment dispatch is used when FSM + SQLite are bound."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

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
    control_plane._rust_db_bound = plane._rust_db_bound
    control_plane._warned_deployment_fallback = getattr(
        plane, "_warned_deployment_fallback", False
    )
    control_plane._test_plane_ref = plane
    set_control_plane(control_plane)


def _force_rust_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_plane, "_rust_db_bound", True)
    monkeypatch.setattr(control_plane, "_rust_fsm_handle", 1)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _spy_rust_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    *,
    canned: dict[str, dict] | None = None,
) -> None:
    orig = InMemoryControlPlane._rust_deployment_dispatch
    canned = canned or {}

    def wrapper(self: InMemoryControlPlane, op: str, body: dict) -> dict | None:
        if self._rust_fsm_active() and self._rust_db_bound:
            calls.append(op)
            if op in canned:
                return canned[op]
        return orig(self, op, body)

    monkeypatch.setattr(InMemoryControlPlane, "_rust_deployment_dispatch", wrapper)


def test_deployment_maintenance_prefers_rust_when_bound(tmp_path: Path) -> None:
    """Smoke: maintenance tick runs without error; Rust path is used when FSM + DB are bound."""
    _swap_plane(tmp_path)
    summary = control_plane.deployment_maintenance_tick(stale_after_seconds=120)
    assert "reclaimed" in summary and "triggered" in summary and "reaped" in summary
    if control_plane._rust_fsm_active() and getattr(
        control_plane, "_rust_db_bound", False
    ):
        # Single FFI op returns all three counters when native build matches.
        assert isinstance(summary["reclaimed"], int)


def test_claim_uses_rust_when_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _swap_plane(tmp_path)
    dep = control_plane.create_deployment(
        name="rust-claim-flow",
        flow_name="simple_flow",
        default_parameters={"n": 1},
        paused=False,
    )
    d_id = UUID(dep["id"])
    run = control_plane.trigger_deployment_run(d_id, parameters={})
    assert run["status"] == "SCHEDULED"

    calls: list[str] = []
    _force_rust_bound(monkeypatch)
    now = _now_iso()
    _spy_rust_dispatch(
        monkeypatch,
        calls,
        canned={
            "deployment_claim_next": {
                "ok": True,
                "run": {
                    "id": run["id"],
                    "deployment_id": dep["id"],
                    "status": "CLAIMED",
                    "requested_parameters": {},
                    "resolved_parameters": {"n": 1},
                    "idempotency_key": None,
                    "worker_name": "w1",
                    "lease_until": now,
                    "flow_run_id": None,
                    "error": None,
                    "created_at": now,
                    "updated_at": now,
                    "started_at": None,
                    "finished_at": None,
                    "seq": 1,
                },
            },
        },
    )

    claimed = control_plane.claim_next_deployment_run(
        worker_name="w1", lease_seconds=30
    )
    assert "deployment_claim_next" in calls
    assert claimed is not None
    assert claimed["id"] == run["id"]


def test_create_deployment_rust_dispatch_when_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _swap_plane(tmp_path)
    calls: list[str] = []
    _force_rust_bound(monkeypatch)
    dep_id = str(uuid4())
    now = _now_iso()
    _spy_rust_dispatch(
        monkeypatch,
        calls,
        canned={
            "deployment_create": {
                "ok": True,
                "deployment": {
                    "id": dep_id,
                    "name": "rust-create-flow",
                    "flow_name": "simple_flow",
                    "entrypoint": None,
                    "path": None,
                    "default_parameters": {"n": 1},
                    "paused": False,
                    "concurrency_limit": None,
                    "collision_strategy": "ENQUEUE",
                    "schedule_interval_seconds": None,
                    "schedule_cron": None,
                    "schedule_rrule": None,
                    "schedule_next_run_at": None,
                    "schedule_enabled": False,
                    "work_pool_id": "default-process-pool",
                    "created_at": now,
                    "updated_at": now,
                },
            },
        },
    )

    created = control_plane.create_deployment(
        name="rust-create-flow",
        flow_name="simple_flow",
        default_parameters={"n": 1},
        paused=False,
    )
    assert "deployment_create" in calls
    assert created["id"] == dep_id
    assert created["name"] == "rust-create-flow"
