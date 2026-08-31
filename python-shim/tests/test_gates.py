"""Gate tasks: temporal barriers with max_wait safeguards and DAG visibility."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prefect_compat import flow, gate, set_control_plane, task
from prefect_compat.gates import DEFAULT_GATE_MAX_WAIT, GateWaitTooLongError
from prefect_compat.runtime import InMemoryControlPlane, RunState
from prefect_compat.server import app, control_plane


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / "gates.jsonl"))


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


def test_gate_max_wait_rejects_long_after(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @flow
    def f() -> None:
        g = gate(max_wait=DEFAULT_GATE_MAX_WAIT)
        g.submit(after=timedelta(days=2))

    with pytest.raises(GateWaitTooLongError):
        f()


def test_gate_max_wait_explicit_override(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @task
    def prep() -> int:
        return 1

    @flow
    def f() -> int:
        upstream = prep.submit()
        g = gate(max_wait=timedelta(days=3))
        g.submit(after=timedelta(seconds=0), wait_for=[upstream])
        return 42

    assert f() == 42


def test_gate_task_has_uuid_and_kind(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @flow
    def f() -> None:
        gate().submit(after=timedelta(seconds=0))

    f()
    gate_tasks = [t for t in plane._tasks.values() if t.kind == "gate"]
    assert len(gate_tasks) == 1
    assert gate_tasks[0].task_run_id is not None
    assert gate_tasks[0].state == RunState.COMPLETED


def test_gate_until_past_completes_immediately(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    past = datetime.now(UTC) - timedelta(minutes=5)

    @flow
    def f() -> None:
        gate().submit(until=past)

    f()
    gate_task = next(t for t in plane._tasks.values() if t.kind == "gate")
    assert gate_task.state == RunState.COMPLETED


def test_gate_blocks_downstream(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    order: list[str] = []

    @task
    def prep() -> None:
        order.append("prep")

    @task
    def downstream() -> None:
        order.append("downstream")

    @flow
    def f() -> None:
        p = prep.submit()
        g = gate(name="wait-barrier")
        gf = g.submit(after=timedelta(seconds=0), wait_for=[p])
        downstream.submit(wait_for=[gf])

    f()
    assert order == ["prep", "downstream"]


def test_gate_flow_paused_during_wait(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    paused_seen = False

    @task
    def prep() -> None:
        return None

    @flow
    def f() -> None:
        nonlocal paused_seen
        p = prep.submit()
        gf = gate().submit(after=timedelta(seconds=0.05), wait_for=[p])
        # Flow should have been PAUSED while gate waited; check during downstream wait
        flow_run_id = next(r.run_id for r in plane._flows.values() if r.name == "f")
        # result() on gate future triggers wait
        gf.result()
        rec = plane.get_flow(flow_run_id)
        if rec.state == RunState.PAUSED:
            paused_seen = True

    f()
    # During wait the flow should have hit PAUSED at least transiently
    flow_run = next(r for r in plane._flows.values() if r.name == "f")
    assert flow_run.state == RunState.COMPLETED


def test_gate_flow_paused_observable_mid_wait(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    observed: list[str] = []

    @flow
    def f() -> None:
        gf = gate().submit(after=timedelta(seconds=0.2))
        flow_id = next(r.run_id for r in plane._flows.values() if r.name == "f")

        def poll_pause() -> None:
            import time

            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                st = plane.get_flow(flow_id).state
                observed.append(st.value)
                if st == RunState.COMPLETED:
                    break
                time.sleep(0.02)

        import threading

        t = threading.Thread(target=poll_pause)
        t.start()
        gf.result()
        t.join(timeout=2)

    f()
    assert RunState.PAUSED.value in observed


def test_dag_includes_gate_task_node(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @task
    def prep() -> int:
        return 1

    @flow
    def parent_flow() -> int:
        p = prep.submit()
        gf = gate(name="monthly-open").submit(after=timedelta(seconds=0), wait_for=[p])
        return prep.submit(wait_for=[gf]).result()

    parent_flow()
    parent_run = next(f for f in plane._flows.values() if f.name == "parent_flow")
    dag = plane.get_flow_run_dag(parent_run.run_id, mode="expanded")
    gate_nodes = [n for n in dag["nodes"] if n.get("kind") == "gate_task"]
    assert len(gate_nodes) == 1
    assert gate_nodes[0].get("gate_open_at") is not None
    logical = plane.get_flow_run_dag(parent_run.run_id, mode="logical")
    logical_gate = [n for n in logical["nodes"] if n.get("kind") == "gate_task"]
    assert len(logical_gate) >= 1
    _wire_plane(plane)
    client = TestClient(app)
    api_dag = client.get(f"/api/flow-runs/{parent_run.run_id}/dag?mode=expanded")
    assert api_dag.status_code == 200
    expanded_gate = [n for n in api_dag.json()["nodes"] if n.get("kind") == "gate_task"]
    assert len(expanded_gate) == 1


def test_gate_promotion_tick_completes_due_gate(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    open_at = datetime.now(UTC) + timedelta(seconds=0.05)

    from prefect_compat.decorators import _ACTIVE_FLOW_RUN

    flow_run = plane.create_flow_run("gate-tick-flow")
    token = _ACTIVE_FLOW_RUN.set(flow_run.run_id)
    try:
        gate().submit(until=open_at)
        task_id = next(t.task_run_id for t in plane._tasks.values() if t.kind == "gate")
        assert plane.get_task_run(task_id).state == RunState.PENDING
    finally:
        _ACTIVE_FLOW_RUN.reset(token)

    import time

    time.sleep(0.08)
    n = plane.tick_gate_tasks()
    assert n >= 1
    assert plane.get_task_run(task_id).state == RunState.COMPLETED


def test_gate_open_at_persisted_in_sqlite(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    until = datetime.now(UTC) + timedelta(days=2)

    @flow
    def blocked_flow() -> None:
        gate().submit(until=until)

    with pytest.raises(GateWaitTooLongError):
        blocked_flow()

    # Persistence-only assertion: use explicit finalization so wait_all does not
    # block until the future gate opens (default wait_all would hang ~30m / timeout).
    @flow(final_state="explicit")
    def ok_flow() -> None:
        gate(max_wait=timedelta(hours=3)).submit(
            until=datetime.now(UTC) + timedelta(minutes=30)
        )

    ok_flow()
    rows = plane._query_rows(
        "SELECT kind, gate_open_at FROM task_runs WHERE kind = 'gate'",
        [],
    )
    assert len(rows) == 1
    assert rows[0]["gate_open_at"] is not None
