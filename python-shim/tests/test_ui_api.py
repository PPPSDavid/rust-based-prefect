from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app, control_plane, mapped_flow


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

