"""P3.2a/b: interrupt modes, drain pause/resume, cancel=terminate docs lock."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from prefect_compat import InterruptMode, flow, set_control_plane, task
from prefect_compat.cancellation import FlowRunCancelled
from prefect_compat.decorators import set_control_plane as set_plane
from prefect_compat.lifecycle import parse_interrupt_mode
from prefect_compat.runtime import FlowRunSchedulingHeld, InMemoryControlPlane, RunState
from prefect_compat.server import app, control_plane


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / "lifecycle.jsonl"))


def _wire(plane: InMemoryControlPlane) -> None:
    control_plane._flows = plane._flows
    control_plane._tasks = plane._tasks
    control_plane._events = plane._events
    control_plane._tokens = plane._tokens
    control_plane._history_path = plane._history_path
    control_plane._sqlite_path = plane._sqlite_path
    control_plane._sqlite_conn = plane._sqlite_conn
    control_plane._manifest_by_task = plane._manifest_by_task
    control_plane._reserved_planned_ids = plane._reserved_planned_ids
    control_plane._flow_results = plane._flow_results
    control_plane._lifecycle_by_flow = plane._lifecycle_by_flow
    control_plane._latest_flow_run_id = plane._latest_flow_run_id
    control_plane._rust_bridge = plane._rust_bridge
    control_plane._rust_fsm_bridge = plane._rust_fsm_bridge
    control_plane._rust_fsm_handle = plane._rust_fsm_handle
    control_plane._rust_native_persistence = plane._rust_native_persistence
    control_plane._rust_db_bound = plane._rust_db_bound
    control_plane._lock = plane._lock
    set_plane(control_plane)


def test_parse_interrupt_mode_requires_explicit() -> None:
    with pytest.raises(ValueError, match="required"):
        parse_interrupt_mode(None)
    with pytest.raises(ValueError, match="required"):
        parse_interrupt_mode("")
    assert parse_interrupt_mode("drain") is InterruptMode.DRAIN
    assert parse_interrupt_mode(InterruptMode.TERMINATE) is InterruptMode.TERMINATE


def test_pause_endpoint_requires_mode(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    _wire(plane)
    started = threading.Event()
    release = threading.Event()

    @task
    def slow() -> str:
        started.set()
        release.wait(timeout=5)
        return "ok"

    @flow
    def f() -> str:
        return slow.submit().result()

    thread = threading.Thread(target=f, daemon=True)
    thread.start()
    assert started.wait(timeout=5)
    run = control_plane.latest_flow()
    assert run is not None
    client = TestClient(app)
    missing = client.post(f"/api/flow-runs/{run.run_id}/pause", json={})
    assert missing.status_code == 422
    bad = client.post(f"/api/flow-runs/{run.run_id}/pause", json={"mode": "soft"})
    assert bad.status_code == 422
    ok = client.post(
        f"/api/flow-runs/{run.run_id}/pause", json={"mode": "drain"}
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["interrupt_mode"] == "drain"
    assert body["lifecycle_action"] == "pause"
    release.set()
    thread.join(timeout=5)


def test_drain_pause_holds_scheduling_until_inflight_finishes(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    started = threading.Event()
    release = threading.Event()

    @task
    def slow() -> str:
        started.set()
        assert release.wait(timeout=5)
        return "done"

    @flow
    def g() -> str:
        return slow.submit().result()

    thread = threading.Thread(target=g, daemon=True)
    thread.start()
    assert started.wait(timeout=5)
    run = plane.latest_flow()
    assert run is not None

    detail = plane.pause_flow_run(run.run_id, mode="drain")
    assert detail["lifecycle_action"] == "pause"
    assert detail["interrupt_mode"] == "drain"
    assert detail["state"] == "RUNNING"
    assert detail.get("pause_drain_pending") is True

    with pytest.raises(FlowRunSchedulingHeld):
        plane.create_task_run(run.run_id, "blocked")

    release.set()
    thread.join(timeout=5)

    for _ in range(50):
        settled = plane.get_flow_run_detail(run.run_id)
        assert settled is not None
        if settled["state"] == "PAUSED":
            break
        time.sleep(0.05)
    settled = plane.get_flow_run_detail(run.run_id)
    assert settled is not None
    assert settled["state"] == "PAUSED"
    assert settled["interrupt_mode"] == "drain"

    resumed = plane.resume_flow_run(run.run_id)
    # In-process body already stored a result with no pending tasks → COMPLETED.
    assert resumed["state"] == "COMPLETED"


def test_terminate_holds_scheduling_before_paused_settles(tmp_path: Path) -> None:
    """Terminate must block new tasks as soon as lifecycle is written (not only at PAUSED)."""
    plane = _plane(tmp_path)
    set_control_plane(plane)
    started = threading.Event()
    release = threading.Event()

    @task
    def slow() -> str:
        started.set()
        release.wait(timeout=5)
        return "x"

    @flow
    def f() -> str:
        return slow.submit().result()

    thread = threading.Thread(target=f, daemon=True)
    thread.start()
    assert started.wait(timeout=5)
    run = plane.latest_flow()
    assert run is not None

    # Simulate the mid-terminate window: lifecycle terminate written, state still RUNNING.
    plane._set_lifecycle(
        run.run_id,
        lifecycle_action="pause",
        interrupt_mode="terminate",
        pause_drain_pending=False,
        lifecycle_summary="Paused (terminate) — in-flight tasks interrupted",
    )
    assert plane.get_flow(run.run_id).state == RunState.RUNNING
    with pytest.raises(FlowRunSchedulingHeld):
        plane.create_task_run(run.run_id, "should-block")

    release.set()
    thread.join(timeout=5)


def test_cancel_sets_terminate_lifecycle(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    started = threading.Event()
    release = threading.Event()

    @task
    def slow() -> str:
        started.set()
        release.wait(timeout=5)
        return "x"

    @flow
    def f() -> str:
        return slow.submit().result()

    def _run() -> None:
        try:
            f()
        except FlowRunCancelled:
            return

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert started.wait(timeout=5)
    run = plane.latest_flow()
    assert run is not None
    detail = plane.cancel_flow_run(run.run_id)
    assert detail["state"] == "CANCELLED"
    assert detail["lifecycle_action"] == "cancel"
    assert detail["interrupt_mode"] == "terminate"
    release.set()
    thread.join(timeout=5)


def test_resume_rejects_gate_only_pause(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    record = plane.create_flow_run("gate-only")
    plane.set_flow_state(record.run_id, RunState.PENDING, uuid4(), "propose")
    plane.set_flow_state(record.run_id, RunState.RUNNING, uuid4(), "start")
    plane.set_flow_state(record.run_id, RunState.PAUSED, uuid4(), "gate_wait")
    with pytest.raises(ValueError, match="operator pause"):
        plane.resume_flow_run(record.run_id)
    with pytest.raises(ValueError, match="gate waits"):
        plane.pause_flow_run(record.run_id, mode="drain")


def test_terminate_pause_fences_late_completed(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    started = threading.Event()
    release = threading.Event()

    @task
    def slow() -> str:
        started.set()
        assert release.wait(timeout=5)
        return "late"

    @flow
    def f() -> str:
        return slow.submit().result()

    thread = threading.Thread(target=f, daemon=True)
    thread.start()
    assert started.wait(timeout=5)
    run = plane.latest_flow()
    assert run is not None
    tasks = list(plane._tasks.values())
    assert tasks
    task_id = tasks[0].task_run_id

    detail = plane.pause_flow_run(run.run_id, mode="terminate")
    assert detail["state"] == "PAUSED"
    assert plane.get_task_run(task_id).state == RunState.CANCELLED

    release.set()
    thread.join(timeout=5)
    # Body returned, but CANCELLED must not resurrect to COMPLETED.
    assert plane.get_task_run(task_id).state == RunState.CANCELLED


def test_drain_resume_completes_when_result_already_stored(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    started = threading.Event()
    release = threading.Event()

    @task
    def slow() -> str:
        started.set()
        assert release.wait(timeout=5)
        return "done"

    @flow
    def g() -> str:
        return slow.submit().result()

    thread = threading.Thread(target=g, daemon=True)
    thread.start()
    assert started.wait(timeout=5)
    run = plane.latest_flow()
    assert run is not None
    plane.pause_flow_run(run.run_id, mode="drain")
    release.set()
    thread.join(timeout=5)

    settled = None
    for _ in range(50):
        settled = plane.get_flow_run_detail(run.run_id)
        assert settled is not None
        if settled["state"] == "PAUSED":
            break
        time.sleep(0.05)
    assert settled is not None
    assert settled["state"] == "PAUSED"
    resumed = plane.resume_flow_run(run.run_id)
    assert resumed["state"] == "COMPLETED"
