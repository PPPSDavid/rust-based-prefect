from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app, control_plane, failing_flow, mapped_flow


def _seed_data(tmp_path: Path) -> str:
    history = tmp_path / "ui-api-history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    # Swap the global plane used by API handlers.
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
    # Other tests call ``set_control_plane`` on a different instance; align the shim.
    set_control_plane(control_plane)
    mapped_flow(3)
    run = control_plane.latest_flow()
    assert run is not None
    return str(run.run_id)


def test_run_and_log_endpoints(tmp_path: Path) -> None:
    flow_run_id = _seed_data(tmp_path)
    client = TestClient(app)

    runs = client.get("/api/flow-runs?limit=10")
    assert runs.status_code == 200
    runs_payload = runs.json()
    assert runs_payload["items"]

    detail = client.get(f"/api/flow-runs/{flow_run_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == flow_run_id

    task_runs = client.get(f"/api/flow-runs/{flow_run_id}/task-runs")
    assert task_runs.status_code == 200
    assert len(task_runs.json()["items"]) > 0

    logs = client.get(f"/api/flow-runs/{flow_run_id}/logs")
    assert logs.status_code == 200
    assert len(logs.json()["items"]) > 0


def test_events_and_artifacts_endpoints(tmp_path: Path) -> None:
    flow_run_id = _seed_data(tmp_path)
    client = TestClient(app)

    events = client.get(f"/api/flow-runs/{flow_run_id}/events")
    assert events.status_code == 200
    assert len(events.json()["items"]) > 0

    artifacts = client.get(f"/api/flow-runs/{flow_run_id}/artifacts")
    assert artifacts.status_code == 200
    assert isinstance(artifacts.json(), list)


def test_dag_endpoint_logical_and_expanded(tmp_path: Path) -> None:
    flow_run_id = _seed_data(tmp_path)
    client = TestClient(app)
    logical = client.get(f"/api/flow-runs/{flow_run_id}/dag?mode=logical")
    assert logical.status_code == 200
    logical_payload = logical.json()
    assert len(logical_payload["nodes"]) > 0
    assert "source" in logical_payload

    expanded = client.get(f"/api/flow-runs/{flow_run_id}/dag?mode=expanded")
    assert expanded.status_code == 200
    expanded_payload = expanded.json()
    assert len(expanded_payload["nodes"]) > 0


def test_dag_marks_not_reachable_after_failure(tmp_path: Path) -> None:
    history = tmp_path / "ui-api-history.jsonl"
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

    try:
        failing_flow(3)
    except Exception:
        pass
    run = control_plane.latest_flow()
    assert run is not None
    client = TestClient(app)
    dag = client.get(f"/api/flow-runs/{run.run_id}/dag?mode=logical")
    assert dag.status_code == 200
    payload = dag.json()
    states = {node["state"] for node in payload["nodes"]}
    assert "FAILED" in states
    assert len(payload["nodes"]) >= 2
    assert payload["source"] in {"forecast", "runtime"}


def test_cors_allows_local_frontend_origin(tmp_path: Path) -> None:
    _ = _seed_data(tmp_path)
    client = TestClient(app)
    res = client.options(
        "/api/flow-runs?limit=10",
        headers={
            "Origin": "http://localhost:4173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:4173"

