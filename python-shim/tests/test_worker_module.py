from __future__ import annotations

from pathlib import Path
from uuid import UUID

from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import FLOW_REGISTRY, control_plane
from prefect_compat.worker import execute_claimed_deployment_run, resolve_flow_callable


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


def test_resolve_flow_callable_builtin() -> None:
    fn = resolve_flow_callable("simple_flow", None, FLOW_REGISTRY)
    assert fn.__name__ == "simple_flow"


def test_execute_claimed_run_completes(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    dep = control_plane.create_deployment(
        name="worker-test",
        flow_name="simple_flow",
        default_parameters={"n": 3},
        paused=False,
    )
    run = control_plane.trigger_deployment_run(UUID(dep["id"]), parameters={"n": 5})
    assert run["status"] == "SCHEDULED"

    claimed = control_plane.claim_next_deployment_run(worker_name="test-worker", lease_seconds=30)
    assert claimed is not None
    assert claimed["id"] == run["id"]

    execute_claimed_deployment_run(control_plane, claimed, FLOW_REGISTRY)

    runs = control_plane.list_deployment_runs(limit=10)
    finished = next(item for item in runs.items if item["id"] == run["id"])
    assert finished["status"] == "COMPLETED"
    assert finished["flow_run_id"] is not None
