from __future__ import annotations

import threading
from pathlib import Path

from fastapi.testclient import TestClient

from prefect_compat.decorators import flow, set_control_plane
from prefect_compat.deploy import deploy, serve
from prefect_compat.deploy.spec import PullStepSpec
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app, control_plane


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
    control_plane._test_plane_ref = plane
    set_control_plane(control_plane)


def test_deploy_upsert_creates_then_updates(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    client = TestClient(app)

    @flow
    def demo(n: int = 1) -> int:
        return n

    created = deploy(
        demo,
        name="api-upsert",
        parameters={"n": 1},
        session=client,
    )
    assert created["action"] == "create"
    assert created["dry_run"] is False
    deployment_id = created["deployment"]["id"]
    assert created["deployment"]["default_parameters"] == {"n": 1}

    updated = deploy(
        demo,
        name="api-upsert",
        parameters={"n": 99},
        session=client,
    )
    assert updated["action"] == "update"
    assert updated["dry_run"] is False
    assert updated["deployment"]["id"] == deployment_id
    assert updated["deployment"]["default_parameters"] == {"n": 99}


def test_serve_deploys_runs_pull_steps_and_worker_loop(
    tmp_path: Path, monkeypatch
) -> None:
    _swap_plane(tmp_path)
    client = TestClient(app)
    stop_event = threading.Event()
    worker_calls: list[dict[str, object]] = []
    pull_calls: list[list[PullStepSpec]] = []

    def fake_run_worker_loop(
        control_plane_arg: object,
        *,
        worker_name: str,
        work_pool_id: str,
        flow_registry: dict[str, object],
        stop_event: threading.Event,
        lease_seconds: int = 30,
        heartbeat_interval: float = 15.0,
    ) -> None:
        worker_calls.append(
            {
                "worker_name": worker_name,
                "work_pool_id": work_pool_id,
                "flow_registry": flow_registry,
            }
        )
        stop_event.set()

    def fake_run_pull_steps(steps: list[PullStepSpec]) -> dict[str, object]:
        pull_calls.append(steps)
        return {}

    monkeypatch.setattr(
        "prefect_compat.deploy.api.run_worker_loop", fake_run_worker_loop
    )
    monkeypatch.setattr("prefect_compat.deploy.api.run_pull_steps", fake_run_pull_steps)

    @flow
    def demo(n: int = 1) -> int:
        return n

    pull_steps = [
        PullStepSpec(
            step="ironflow.deployments.steps.set_working_directory",
            inputs={"directory": str(tmp_path)},
        )
    ]

    serve(
        demo,
        name="api-serve",
        pull_steps=pull_steps,
        session=client,
        stop_event=stop_event,
        worker_name="test-serve-worker",
    )

    assert len(pull_calls) == 1
    assert pull_calls[0] == pull_steps
    assert len(worker_calls) == 1
    assert worker_calls[0]["worker_name"] == "test-serve-worker"
    flow_registry = worker_calls[0]["flow_registry"]
    assert isinstance(flow_registry, dict)
    assert "demo" in flow_registry

    deployment = client.get("/api/deployments/by-name/api-serve").json()
    assert deployment["name"] == "api-serve"
    assert deployment["default_parameters"] == {}
