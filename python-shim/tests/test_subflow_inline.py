"""Phase 2: blocking inline subflows (mechanism 1)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from prefect_compat import InMemoryControlPlane, deployment_ref, flow, set_control_plane, task
from prefect_compat.cancellation import FlowRunCancelled, assert_flow_not_cancelled, sleep_cancelable
from prefect_compat.runtime import RunState
from prefect_compat.worker import run_worker_loop


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / "subflow-inline.jsonl"))


def test_inline_subflow_linked_flow_run(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @flow
    def child(n: int) -> int:
        return n + 1

    @flow
    def parent(x: int) -> int:
        return child(x)

    assert parent(5) == 6
    parents = [f for f in plane._flows.values() if f.name == "parent"]
    children = [f for f in plane._flows.values() if f.name == "child"]
    assert len(parents) == 1
    assert len(children) == 1
    child_run = children[0]
    assert child_run.execution_mode == "inline"
    assert child_run.parent_flow_run_id == parents[0].run_id
    assert child_run.depth == 1


def test_inline_subflow_tasks_attach_to_child_run(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @task
    def inc(x: int) -> int:
        return x + 1

    @flow
    def child(n: int) -> int:
        return inc.submit(n).result()

    @flow
    def parent() -> int:
        return child(10)

    assert parent() == 11
    child_runs = [f for f in plane._flows.values() if f.name == "child"]
    child_tasks = [t for t in plane._tasks.values() if t.flow_run_id == child_runs[0].run_id]
    parent_tasks = [t for t in plane._tasks.values() if t.task_name == "inc" and t.flow_run_id != child_runs[0].run_id]
    assert len(child_tasks) == 1
    assert child_tasks[0].task_name == "inc"
    assert len(parent_tasks) == 0


def test_inline_subflow_downstream_via_python_value(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @flow
    def child(n: int) -> int:
        return n + 1

    @task
    def downstream(x: int) -> int:
        return x * 2

    @flow
    def parent() -> int:
        return downstream.submit(child(5)).result()

    assert parent() == 12


def test_nested_inline_subflow_depth(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @flow
    def leaf(n: int) -> int:
        return n + 1

    @flow
    def mid(n: int) -> int:
        return leaf(n)

    @flow
    def root(n: int) -> int:
        return mid(n)

    assert root(1) == 2
    leaf_run = next(f for f in plane._flows.values() if f.name == "leaf")
    mid_run = next(f for f in plane._flows.values() if f.name == "mid")
    root_run = next(f for f in plane._flows.values() if f.name == "root")
    assert root_run.depth == 0
    assert mid_run.depth == 1
    assert leaf_run.depth == 2
    assert leaf_run.root_flow_run_id == root_run.run_id


def test_inline_inside_parent_with_deployment_subflow(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}

    @flow
    def child_deployed(n: int) -> int:
        return n * 3

    @flow
    def mid(n: int) -> int:
        inline = n + 1
        return deployment_ref("child-deploy").submit(n=inline).result()

    @flow
    def root() -> int:
        return mid(2)

    registry["child_deployed"] = child_deployed
    registry["mid"] = mid
    registry["root"] = root

    plane.create_deployment(
        name="child-deploy",
        flow_name="child_deployed",
        default_parameters={},
        paused=False,
    )
    stop = threading.Event()
    worker = threading.Thread(
        target=run_worker_loop,
        kwargs={
            "control_plane": plane,
            "worker_name": "inline-mix-worker",
            "work_pool_id": "default-process-pool",
            "flow_registry": registry,
            "lease_seconds": 30,
            "stop_event": stop,
        },
        daemon=True,
    )
    worker.start()
    try:
        assert root() == 9  # (2+1)*3
    finally:
        stop.set()
        worker.join(timeout=5)

    mid_runs = [f for f in plane._flows.values() if f.name == "mid"]
    assert any(r.execution_mode == "inline" for r in mid_runs)


def test_parent_cancel_propagates_to_inline_child(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    parent_run_id: list = []

    @flow
    def child() -> None:
        try:
            sleep_cancelable(5.0, poll_seconds=0.05)
        except FlowRunCancelled:
            raise

    @flow
    def parent() -> None:
        from prefect_compat.decorators import _ACTIVE_FLOW_RUN

        parent_run_id.append(_ACTIVE_FLOW_RUN.get())
        child()

    def cancel_soon() -> None:
        time.sleep(0.15)
        if parent_run_id:
            plane.cancel_flow_run(parent_run_id[0])

    threading.Thread(target=cancel_soon, daemon=True).start()
    with pytest.raises(FlowRunCancelled):
        parent()

    child_runs = [f for f in plane._flows.values() if f.name == "child"]
    assert len(child_runs) == 1
    assert child_runs[0].state == RunState.CANCELLED
