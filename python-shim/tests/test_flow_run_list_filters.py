"""B1: GET /api/flow-runs filters, sort, and keyset cursors."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane, RunState
from prefect_compat.server import app, control_plane


def _swap_plane(tmp_path: Path) -> InMemoryControlPlane:
    history = tmp_path / "flow-run-list-history.jsonl"
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
    return plane


def _to_state(run_id, target: RunState) -> None:
    version = 0
    for state in (RunState.PENDING, RunState.RUNNING, target):
        if state == RunState.PENDING and target == RunState.SCHEDULED:
            return
        control_plane.set_flow_state(
            run_id, state, uuid4(), f"to-{state.value}", expected_version=version
        )
        version += 1
        if state == target:
            break


def test_list_flow_runs_q_and_state_filter(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    a = control_plane.create_flow_run("demo_a")
    other = control_plane.create_flow_run("other")
    b = control_plane.create_flow_run("demo_b")
    _to_state(a.run_id, RunState.COMPLETED)
    _to_state(other.run_id, RunState.FAILED)
    _to_state(b.run_id, RunState.FAILED)
    client = TestClient(app)
    res = client.get(
        "/api/flow-runs", params={"q": "demo", "state": "FAILED", "limit": 50}
    )
    assert res.status_code == 200, res.text
    names = [item["name"] for item in res.json()["items"]]
    assert names == ["demo_b"]


def test_list_flow_runs_sort_name_cursor_walk(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    control_plane.create_flow_run("alpha")
    control_plane.create_flow_run("beta")
    control_plane.create_flow_run("gamma")
    client = TestClient(app)
    page1 = client.get(
        "/api/flow-runs", params={"sort": "name", "order": "asc", "limit": 2}
    )
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert [i["name"] for i in body1["items"]] == ["alpha", "beta"]
    assert body1["next_cursor"]
    page2 = client.get(
        "/api/flow-runs",
        params={
            "sort": "name",
            "order": "asc",
            "limit": 2,
            "cursor": body1["next_cursor"],
        },
    )
    assert page2.status_code == 200, page2.text
    body2 = page2.json()
    assert [i["name"] for i in body2["items"]] == ["gamma"]
    assert body2["next_cursor"] is None


def test_list_flow_runs_cursor_sort_mismatch_400(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    client = TestClient(app)
    res = client.get(
        "/api/flow-runs",
        params={"sort": "name", "order": "asc", "cursor": "12"},
    )
    assert res.status_code == 400


def test_list_flow_runs_python_fallback_parity(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    control_plane.create_flow_run("zeta")
    control_plane.create_flow_run("alpha")
    control_plane.create_flow_run("mu")
    rust_page = control_plane.list_flow_runs(sort="name", order="asc", limit=2, q="a")
    bridge = control_plane._rust_bridge
    control_plane._rust_bridge = None
    try:
        py_page = control_plane.list_flow_runs(sort="name", order="asc", limit=2, q="a")
    finally:
        control_plane._rust_bridge = bridge
    assert [i["name"] for i in rust_page.items] == [i["name"] for i in py_page.items]
    assert bool(rust_page.next_cursor) == bool(py_page.next_cursor)
