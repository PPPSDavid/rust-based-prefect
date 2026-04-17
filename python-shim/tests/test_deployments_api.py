from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import _run_local_deployment_once, app, control_plane


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


def test_deployment_trigger_and_local_worker(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    client = TestClient(app)

    create = client.post(
        "/api/deployments",
        json={
            "name": "simple-flow-e2e",
            "flow_name": "simple_flow",
            "default_parameters": {"n": 5},
            "paused": False,
        },
    )
    assert create.status_code == 200
    deployment_id = create.json()["id"]

    queued = client.post(
        f"/api/deployments/{deployment_id}/run",
        json={"parameters": {"n": 7}, "idempotency_key": "abc-123"},
    )
    assert queued.status_code == 200
    queued_id = queued.json()["id"]
    assert queued.json()["status"] == "SCHEDULED"

    second = client.post(
        f"/api/deployments/{deployment_id}/run",
        json={"parameters": {"n": 7}, "idempotency_key": "abc-123"},
    )
    assert second.status_code == 200
    assert second.json()["id"] == queued_id

    handled = _run_local_deployment_once(worker_name="test-worker")
    assert handled is True

    runs = client.get("/api/deployment-runs?limit=10")
    assert runs.status_code == 200
    items = runs.json()["items"]
    assert items
    assert items[0]["id"] == queued_id
    assert items[0]["status"] == "COMPLETED"
    assert items[0]["flow_run_id"] is not None
