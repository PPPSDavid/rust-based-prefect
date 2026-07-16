"""Tier B2 HTTP worker claim / started / finished protocol."""

from __future__ import annotations

import time
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import FLOW_REGISTRY, app, control_plane
from prefect_compat.worker import (
    HttpWorkerBackend,
    execute_claimed_deployment_run,
    resolve_worker_mode,
)
from prefect_compat.worker_client import WorkerHttpClient


def _swap_plane(tmp_path: Path) -> InMemoryControlPlane:
    history = tmp_path / "http-worker-history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    control_plane._flows = plane._flows
    control_plane._tasks = plane._tasks
    control_plane._events = plane._events
    control_plane._tokens = plane._tokens
    control_plane._history_path = plane._history_path
    control_plane._sqlite_path = plane._sqlite_path
    control_plane._sqlite_conn = plane._sqlite_conn
    control_plane._store = plane._store
    control_plane._manifest_by_task = plane._manifest_by_task
    control_plane._rust_bridge = plane._rust_bridge
    control_plane._rust_fsm_bridge = plane._rust_fsm_bridge
    control_plane._rust_fsm_handle = plane._rust_fsm_handle
    control_plane._rust_native_persistence = plane._rust_native_persistence
    control_plane._rust_db_bound = plane._rust_db_bound
    control_plane._test_plane_ref = plane
    set_control_plane(control_plane)
    return plane


def test_resolve_worker_mode_defaults_file(monkeypatch) -> None:
    monkeypatch.delenv("IRONFLOW_WORKER_MODE", raising=False)
    assert resolve_worker_mode() == "file"
    monkeypatch.setenv("IRONFLOW_WORKER_MODE", "HTTP")
    assert resolve_worker_mode() == "http"
    assert resolve_worker_mode("file") == "file"


def test_http_claim_empty_queue_returns_204(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    client = TestClient(app)
    response = client.post(
        "/api/workers/claim",
        json={"worker_name": "w1", "lease_seconds": 30},
    )
    assert response.status_code == 204


def test_http_claim_exclusivity_and_enrichment(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    dep = control_plane.create_deployment(
        name="http-claim-test",
        flow_name="simple_flow",
        default_parameters={"n": 1},
        paused=False,
    )
    run = control_plane.trigger_deployment_run(UUID(dep["id"]), parameters={"n": 2})
    client = TestClient(app)

    first = client.post(
        "/api/workers/claim",
        json={"worker_name": "worker-a", "lease_seconds": 60},
    )
    assert first.status_code == 200
    claimed = first.json()
    assert claimed["id"] == run["id"]
    assert claimed["status"] == "CLAIMED"
    assert claimed["deployment"]["flow_name"] == "simple_flow"
    assert claimed["deployment"]["id"] == dep["id"]

    second = client.post(
        "/api/workers/claim",
        json={"worker_name": "worker-b", "lease_seconds": 60},
    )
    assert second.status_code == 204


def test_http_claim_work_pool_filter(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    control_plane.create_work_pool(name="pool-a", pool_type="process")
    control_plane.create_work_pool(name="pool-b", pool_type="process")
    pools = {p["name"]: p["id"] for p in control_plane.list_work_pools(limit=50).items}
    dep = control_plane.create_deployment(
        name="pool-filter-dep",
        flow_name="simple_flow",
        work_pool_id=pools["pool-a"],
        default_parameters={"n": 1},
        paused=False,
    )
    control_plane.trigger_deployment_run(UUID(dep["id"]))
    client = TestClient(app)

    miss = client.post(
        "/api/workers/claim",
        json={
            "worker_name": "w-b",
            "work_pool_id": pools["pool-b"],
            "lease_seconds": 30,
        },
    )
    assert miss.status_code == 204

    hit = client.post(
        "/api/workers/claim",
        json={
            "worker_name": "w-a",
            "work_pool_id": pools["pool-a"],
            "lease_seconds": 30,
        },
    )
    assert hit.status_code == 200
    assert hit.json()["deployment_id"] == dep["id"]


def test_http_lease_expiry_reclaim(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    dep = control_plane.create_deployment(
        name="lease-reclaim",
        flow_name="simple_flow",
        default_parameters={"n": 1},
        paused=False,
    )
    run = control_plane.trigger_deployment_run(UUID(dep["id"]))
    client = TestClient(app)

    claimed = client.post(
        "/api/workers/claim",
        json={"worker_name": "slow-worker", "lease_seconds": 1},
    )
    assert claimed.status_code == 200
    assert claimed.json()["id"] == run["id"]

    time.sleep(1.2)
    control_plane.deployment_maintenance_tick(stale_after_seconds=1)

    reclaimed = client.post(
        "/api/workers/claim",
        json={"worker_name": "other-worker", "lease_seconds": 30},
    )
    assert reclaimed.status_code == 200
    assert reclaimed.json()["id"] == run["id"]
    assert reclaimed.json()["worker_name"] == "other-worker"


def test_worker_http_client_execute_roundtrip(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    dep = control_plane.create_deployment(
        name="http-exec",
        flow_name="simple_flow",
        default_parameters={"n": 4},
        paused=False,
    )
    control_plane.trigger_deployment_run(UUID(dep["id"]), parameters={"n": 7})
    http = TestClient(app)
    client = WorkerHttpClient(session=http)

    claimed = client.claim("http-worker", lease_seconds=30)
    assert claimed is not None
    assert claimed["deployment"]["flow_name"] == "simple_flow"

    execute_claimed_deployment_run(HttpWorkerBackend(client), claimed, FLOW_REGISTRY)

    finished = client.get_deployment_run(claimed["id"])
    assert finished is not None
    assert finished["status"] == "COMPLETED"
    assert finished["flow_run_id"] is not None
