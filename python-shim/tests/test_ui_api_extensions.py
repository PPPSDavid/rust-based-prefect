from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app, control_plane, mapped_flow


def _seed_data(tmp_path: Path) -> str:
    history = tmp_path / "ui-api-ext-history.jsonl"
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
    control_plane._lock = plane._lock
    control_plane._test_plane_ref = plane
    set_control_plane(control_plane)
    mapped_flow(3)
    control_plane.create_deployment(name="mapped_flow-ui-test", flow_name="mapped_flow")
    run = control_plane.latest_flow()
    assert run is not None
    return str(run.run_id)


def test_get_deployment_endpoint(tmp_path: Path) -> None:
    _seed_data(tmp_path)
    client = TestClient(app)
    deployments = client.get("/api/deployments?limit=10")
    assert deployments.status_code == 200
    items = deployments.json()["items"]
    assert items
    dep_id = items[0]["id"]
    detail = client.get(f"/api/deployments/{dep_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == dep_id


def test_cancel_flow_run_endpoint(tmp_path: Path) -> None:
    _seed_data(tmp_path)
    client = TestClient(app)
    record = control_plane.create_flow_run("mapped_flow")
    flow_run_id = str(record.run_id)
    cancel = client.post(f"/api/flow-runs/{flow_run_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["state"] == "CANCELLED"
    again = client.post(f"/api/flow-runs/{flow_run_id}/cancel")
    assert again.status_code == 200
    assert again.json()["state"] == "CANCELLED"


def test_retry_flow_run_requires_deployment(tmp_path: Path) -> None:
    _seed_data(tmp_path)
    client = TestClient(app)
    record = control_plane.create_flow_run("mapped_flow")
    retry = client.post(f"/api/flow-runs/{record.run_id}/retry")
    assert retry.status_code == 409


def test_retry_flow_run_from_deployment(tmp_path: Path) -> None:
    _seed_data(tmp_path)
    client = TestClient(app)
    deployments = client.get("/api/deployments?limit=1").json()["items"]
    dep_id = deployments[0]["id"]
    triggered = client.post(
        f"/api/deployments/{dep_id}/run", json={"parameters": {"n": 2}}
    )
    assert triggered.status_code == 200
    dep_run = triggered.json()
    flow_run = control_plane.create_flow_run("mapped_flow")
    control_plane._sqlite_conn.execute(
        "UPDATE deployment_runs SET flow_run_id = ? WHERE id = ?",
        [str(flow_run.run_id), dep_run["id"]],
    )
    retry = client.post(f"/api/flow-runs/{flow_run.run_id}/retry")
    assert retry.status_code == 200
    assert retry.json()["deployment_id"] == dep_id


def test_work_pools_and_workers_endpoints(tmp_path: Path) -> None:
    _seed_data(tmp_path)
    client = TestClient(app)
    pools = client.get("/api/work-pools")
    assert pools.status_code == 200
    assert any(p["id"] == "default-process-pool" for p in pools.json()["items"])
    created = client.post(
        "/api/work-pools", json={"name": "test-pool", "type": "process"}
    )
    assert created.status_code == 200
    pool_id = created.json()["id"]
    heartbeat = client.post(
        "/api/workers/heartbeat",
        json={"name": "test-worker", "work_pool_id": pool_id},
    )
    assert heartbeat.status_code == 200
    workers = client.get(f"/api/workers?work_pool_id={pool_id}")
    assert workers.status_code == 200
    assert any(w["name"] == "test-worker" for w in workers.json()["items"])
