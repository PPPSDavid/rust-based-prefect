"""Phase 4: DAG node kinds and run detail breadcrumbs for subflows."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi.testclient import TestClient

from prefect_compat import InMemoryControlPlane, deployment_ref, flow, set_control_plane
from prefect_compat.server import app, control_plane
from prefect_compat.worker import run_worker_loop


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / "subflow-dag-ui.jsonl"))


def _wire_plane(plane: InMemoryControlPlane) -> None:
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
    control_plane._rust_bridge = plane._rust_bridge
    control_plane._rust_fsm_bridge = plane._rust_fsm_bridge
    control_plane._rust_fsm_handle = plane._rust_fsm_handle
    control_plane._rust_native_persistence = plane._rust_native_persistence
    control_plane._rust_db_bound = plane._rust_db_bound
    control_plane._lock = plane._lock
    set_control_plane(control_plane)


def test_dag_includes_inline_subflow_node(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @flow
    def child(n: int) -> int:
        return n + 1

    @flow
    def parent() -> int:
        return child(5)

    parent()
    _wire_plane(plane)
    parent_run = next(f for f in plane._flows.values() if f.name == "parent")
    client = TestClient(app)
    dag = client.get(f"/api/flow-runs/{parent_run.run_id}/dag?mode=logical")
    assert dag.status_code == 200
    payload = dag.json()
    inline_nodes = [n for n in payload["nodes"] if n.get("kind") == "inline_subflow"]
    assert len(inline_nodes) == 1
    assert inline_nodes[0]["child_flow_run_id"]
    assert inline_nodes[0]["execution_mode"] == "inline"
    assert inline_nodes[0]["label"] == "child"


def test_dag_includes_subflow_task_node(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}
    stop = threading.Event()

    @flow
    def child_flow() -> int:
        return 7

    @flow
    def parent_flow() -> int:
        fut = deployment_ref("child-deploy").submit()
        return fut.result()

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow

    plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    worker = threading.Thread(
        target=run_worker_loop,
        kwargs={
            "control_plane": plane,
            "worker_name": "dag-ui-worker",
            "work_pool_id": "default-process-pool",
            "flow_registry": registry,
            "lease_seconds": 30,
            "stop_event": stop,
        },
        daemon=True,
    )
    worker.start()
    try:
        assert parent_flow() == 7
        parent_run = next(f for f in plane._flows.values() if f.name == "parent_flow")
        dag = plane.get_flow_run_dag(parent_run.run_id, mode="logical")
        subflow_nodes = [n for n in dag["nodes"] if n.get("kind") == "subflow_task"]
        assert len(subflow_nodes) == 1
        assert subflow_nodes[0].get("child_flow_run_id")
        assert subflow_nodes[0]["kind"] == "subflow_task"
        _wire_plane(plane)
        client = TestClient(app)
        api_dag = client.get(f"/api/flow-runs/{parent_run.run_id}/dag?mode=logical")
        assert api_dag.status_code == 200
        api_subflow = [n for n in api_dag.json()["nodes"] if n.get("kind") == "subflow_task"]
        assert len(api_subflow) == 1
    finally:
        stop.set()
        worker.join(timeout=5)


def test_flow_run_detail_breadcrumb_and_children_summary(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @flow
    def leaf(n: int) -> int:
        return n

    @flow
    def mid(n: int) -> int:
        return leaf(n)

    @flow
    def root() -> int:
        return mid(3)

    root()
    _wire_plane(plane)
    root_run = next(f for f in plane._flows.values() if f.name == "root")
    mid_run = next(f for f in plane._flows.values() if f.name == "mid")
    client = TestClient(app)
    detail = client.get(f"/api/flow-runs/{mid_run.run_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["parent_flow_run_id"] == str(root_run.run_id)
    assert payload["breadcrumb"][-1]["id"] == str(mid_run.run_id)
    assert payload["breadcrumb"][0]["id"] == str(root_run.run_id)
    assert payload["children_summary"]["inline_subflows"] == 1

    root_detail = client.get(f"/api/flow-runs/{root_run.run_id}").json()
    assert root_detail["children_summary"]["inline_subflows"] == 1
    assert root_detail["breadcrumb"][-1]["id"] == str(root_run.run_id)


def test_inline_child_dag_fetchable_for_mini_view(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @flow
    def child() -> int:
        return 1

    @flow
    def parent() -> int:
        return child()

    parent()
    _wire_plane(plane)
    child_run = next(f for f in plane._flows.values() if f.name == "child")
    client = TestClient(app)
    child_dag = client.get(f"/api/flow-runs/{child_run.run_id}/dag?mode=logical")
    assert child_dag.status_code == 200
    assert isinstance(child_dag.json()["nodes"], list)


def test_deployment_subflow_linkage_survives_history_reload(tmp_path: Path) -> None:
    history = tmp_path / "subflow-reload.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    set_control_plane(plane)
    registry: dict = {}
    stop = threading.Event()

    @flow
    def child_flow() -> int:
        return 11

    @flow
    def parent_flow() -> int:
        return deployment_ref("child-deploy").submit().result()

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow
    plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    worker = threading.Thread(
        target=run_worker_loop,
        kwargs={
            "control_plane": plane,
            "worker_name": "reload-worker",
            "work_pool_id": "default-process-pool",
            "flow_registry": registry,
            "lease_seconds": 30,
            "stop_event": stop,
        },
        daemon=True,
    )
    worker.start()
    try:
        assert parent_flow() == 11
        parent_run = next(f for f in plane._flows.values() if f.name == "parent_flow")
        parent_id = parent_run.run_id
    finally:
        stop.set()
        worker.join(timeout=5)

    reloaded = InMemoryControlPlane(history_path=str(history))
    dag = reloaded.get_flow_run_dag(parent_id, mode="logical")
    subflow_nodes = [n for n in dag["nodes"] if n.get("kind") == "subflow_task"]
    assert len(subflow_nodes) == 1
    assert subflow_nodes[0].get("child_flow_run_id")
    assert all(n.get("task_name") != "unknown_task" for n in dag["nodes"])

    task_rows = reloaded._query_rows(
        "SELECT child_flow_run_id, child_deployment_run_id FROM task_runs WHERE flow_run_id = ?",
        [str(parent_id)],
    )
    assert len(task_rows) == 1
    assert task_rows[0]["child_flow_run_id"]
    assert task_rows[0]["child_deployment_run_id"]


def test_flow_run_detail_includes_queryable_child_runs(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}
    stop = threading.Event()

    @flow
    def child_flow(n: int) -> int:
        return n + 1

    @flow
    def parent_flow() -> int:
        inline = child_flow(1)
        return deployment_ref("child-deploy").submit(n=inline).result()

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow

    @flow
    def child_deploy(n: int = 0) -> int:
        return n + 2

    registry["child_deploy"] = child_deploy
    plane.create_deployment(
        name="child-deploy",
        flow_name="child_deploy",
        default_parameters={},
        paused=False,
    )
    worker = threading.Thread(
        target=run_worker_loop,
        kwargs={
            "control_plane": plane,
            "worker_name": "children-nav-worker",
            "work_pool_id": "default-process-pool",
            "flow_registry": registry,
            "lease_seconds": 30,
            "stop_event": stop,
        },
        daemon=True,
    )
    worker.start()
    try:
        parent_flow()
        parent_run = next(f for f in plane._flows.values() if f.name == "parent_flow")
        inline_child = next(
            f
            for f in plane._flows.values()
            if f.name == "child_flow" and f.execution_mode == "inline"
        )
        deploy_child = next(
            f
            for f in plane._flows.values()
            if f.name == "child_deploy" and f.execution_mode == "deployment"
        )
    finally:
        stop.set()
        worker.join(timeout=5)

    _wire_plane(plane)
    client = TestClient(app)
    parent_detail = client.get(f"/api/flow-runs/{parent_run.run_id}").json()
    assert len(parent_detail["children"]) == 2
    child_ids = {child["id"] for child in parent_detail["children"]}
    assert str(inline_child.run_id) in child_ids
    assert str(deploy_child.run_id) in child_ids

    inline_detail = client.get(f"/api/flow-runs/{inline_child.run_id}").json()
    assert inline_detail["id"] == str(inline_child.run_id)
    assert inline_detail["parent_flow_run_id"] == str(parent_run.run_id)
    assert inline_detail["execution_mode"] == "inline"
    assert inline_detail["breadcrumb"][0]["id"] == str(parent_run.run_id)
    assert inline_detail["breadcrumb"][-1]["id"] == str(inline_child.run_id)

    list_resp = client.get("/api/flow-runs?limit=50").json()
    listed_ids = {item["id"] for item in list_resp["items"]}
    assert str(inline_child.run_id) in listed_ids
    assert str(deploy_child.run_id) in listed_ids


def test_deployment_subflow_dag_has_no_unknown_task_phantom(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}
    stop = threading.Event()

    @flow
    def child_flow() -> int:
        return 3

    @flow
    def parent_flow() -> int:
        return deployment_ref("child-deploy").submit().result()

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow
    plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    worker = threading.Thread(
        target=run_worker_loop,
        kwargs={
            "control_plane": plane,
            "worker_name": "phantom-worker",
            "work_pool_id": "default-process-pool",
            "flow_registry": registry,
            "lease_seconds": 30,
            "stop_event": stop,
        },
        daemon=True,
    )
    worker.start()
    try:
        assert parent_flow() == 3
        parent_run = next(f for f in plane._flows.values() if f.name == "parent_flow")
        dag = plane.get_flow_run_dag(parent_run.run_id, mode="logical")
        assert all(n.get("task_name") != "unknown_task" for n in dag["nodes"])
        subflow_nodes = [n for n in dag["nodes"] if n.get("kind") == "subflow_task"]
        assert len(subflow_nodes) == 1
        assert subflow_nodes[0].get("child_flow_run_id")
    finally:
        stop.set()
        worker.join(timeout=5)
