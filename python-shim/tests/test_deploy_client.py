from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from prefect_compat.decorators import set_control_plane
from prefect_compat.deploy import DeployClient, DeploymentSpec
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


def test_upsert_creates_then_updates_same_id(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    client = DeployClient(session=TestClient(app))

    spec = DeploymentSpec(
        name="client-upsert",
        flow_name="simple_flow",
        default_parameters={"n": 1},
        paused=False,
    )

    created = client.upsert_deployment(spec)
    assert created["action"] == "create"
    assert created["dry_run"] is False
    deployment_id = created["deployment"]["id"]
    assert created["deployment"]["default_parameters"] == {"n": 1}

    updated_spec = DeploymentSpec(
        name="client-upsert",
        flow_name="simple_flow",
        default_parameters={"n": 99},
        paused=True,
    )
    updated = client.upsert_deployment(updated_spec)
    assert updated["action"] == "update"
    assert updated["dry_run"] is False
    assert updated["deployment"]["id"] == deployment_id
    assert updated["deployment"]["default_parameters"] == {"n": 99}
    assert updated["deployment"]["paused"] is True


def test_upsert_dry_run_returns_action_without_mutating(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    client = DeployClient(session=TestClient(app))

    spec = DeploymentSpec(
        name="client-dry-run",
        flow_name="simple_flow",
        default_parameters={"n": 1},
        paused=False,
    )

    preview = client.upsert_deployment(spec, dry_run=True)
    assert preview["action"] == "create"
    assert preview["dry_run"] is True
    assert preview["deployment_id"] is None
    assert client.find_deployment_by_name("client-dry-run") is None

    created = client.upsert_deployment(spec)
    assert created["action"] == "create"
    deployment_id = created["deployment"]["id"]

    update_preview = client.upsert_deployment(
        DeploymentSpec(
            name="client-dry-run",
            flow_name="simple_flow",
            default_parameters={"n": 42},
            paused=True,
        ),
        dry_run=True,
    )
    assert update_preview["action"] == "update"
    assert update_preview["dry_run"] is True
    assert update_preview["deployment_id"] == deployment_id

    unchanged = client.find_deployment_by_name("client-dry-run")
    assert unchanged is not None
    assert unchanged["id"] == deployment_id
    assert unchanged["default_parameters"] == {"n": 1}
    assert unchanged["paused"] is False
