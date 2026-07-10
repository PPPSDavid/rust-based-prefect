"""Phase 1: deployment-backed subflow submit, wait, and wait_for integration."""

from __future__ import annotations

import threading
from pathlib import Path
from uuid import UUID

import pytest

from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task, wait
from prefect_compat.decorators import _ACTIVE_FLOW_RUN
from prefect_compat.runtime import RunState
from prefect_compat.subflows import SubflowFuture, deployment_ref
from prefect_compat.worker import run_local_deployment_once, run_worker_loop


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / "subflow-p1.jsonl"))


def _start_worker(plane: InMemoryControlPlane, registry: dict, pool_id: str = "default-process-pool"):
    stop = threading.Event()
    thread = threading.Thread(
        target=run_worker_loop,
        kwargs={
            "control_plane": plane,
            "worker_name": "subflow-test-worker",
            "work_pool_id": pool_id,
            "flow_registry": registry,
            "lease_seconds": 30,
            "stop_event": stop,
        },
        daemon=True,
    )
    thread.start()
    return stop, thread


def test_deployment_ref_submit_wait_same_pool(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}

    @flow
    def child_flow(n: int) -> int:
        return n + 1

    @flow
    def parent_flow(x: int) -> int:
        handle = deployment_ref("child-deploy")
        fut = handle.submit(n=x)
        return fut.result()

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow

    dep = plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    pool_id = dep.get("work_pool_id") or "default-process-pool"
    stop, thread = _start_worker(plane, registry, pool_id)
    try:
        assert parent_flow(5) == 6
    finally:
        stop.set()
        thread.join(timeout=5)

    child_runs = [
        r
        for r in plane._flows.values()
        if r.name == "child_flow" and r.execution_mode == "deployment"
    ]
    assert len(child_runs) == 1
    assert child_runs[0].depth == 1
    assert child_runs[0].parent_flow_run_id is not None


def test_subflow_wait_for_downstream(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}

    @flow
    def child_flow(n: int) -> int:
        return n * 2

    @task
    def downstream(x: int) -> int:
        return x + 10

    @flow
    def parent_flow() -> int:
        fut = deployment_ref("child-deploy").submit(n=3)
        out = downstream.submit(fut, wait_for=[fut]).result()
        return out

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow

    dep = plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    pool_id = dep.get("work_pool_id") or "default-process-pool"
    stop, thread = _start_worker(plane, registry, pool_id)
    try:
        assert parent_flow() == 16
    finally:
        stop.set()
        thread.join(timeout=5)


def test_subflow_fire_and_forget(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}
    seen: list[str] = []

    @flow
    def child_flow(n: int) -> int:
        seen.append("child")
        return n

    @flow
    def parent_flow() -> str:
        deployment_ref("child-deploy").submit(n=1)
        seen.append("parent_done")
        return "ok"

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow

    plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    # No worker — parent should return without waiting for child
    assert parent_flow() == "ok"
    assert seen == ["parent_done"]

    # Child can still be executed later
    assert run_local_deployment_once(plane, "w1", "default-process-pool", registry)
    assert seen == ["parent_done", "child"]


def test_subflow_failed_child_raises(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}

    @flow
    def child_flow() -> None:
        raise ValueError("child boom")

    @flow
    def parent_flow() -> None:
        deployment_ref("child-deploy").submit().result()

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow

    plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    stop, thread = _start_worker(plane, registry)
    try:
        with pytest.raises(RuntimeError, match="child boom"):
            parent_flow()
    finally:
        stop.set()
        thread.join(timeout=5)


def test_surrogate_subflow_task_state(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}
    captured_task_id: list[str] = []

    @flow
    def child_flow() -> int:
        return 42

    @flow
    def parent_flow() -> None:
        fut = deployment_ref("child-deploy").submit()
        captured_task_id.append(fut.parent_task_run_id)
        fut.result()

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow

    plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    stop, thread = _start_worker(plane, registry)
    try:
        parent_flow()
    finally:
        stop.set()
        thread.join(timeout=5)

    task = plane.get_task_run(UUID(captured_task_id[0]))
    assert task.kind == "subflow"
    assert task.state == RunState.COMPLETED
    assert task.child_deployment_run_id is not None
    assert task.child_flow_run_id is not None


def test_recursive_deploy_chain_returns_aggregate_result(tmp_path: Path) -> None:
    """Depth-3 recursive deployment subflows must return the root flow result, not a nested leaf."""
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}
    child_name = "chain-child-deploy"

    @flow
    def chain_child(k: int = 0) -> int:
        if k <= 0:
            return 1
        return deployment_ref(child_name).submit(k=k - 1).result() + 1

    @flow
    def parent_flow() -> int:
        return deployment_ref(child_name).submit(k=2).result()

    registry["chain_child"] = chain_child
    registry["parent_flow"] = parent_flow

    dep = plane.create_deployment(
        name=child_name,
        flow_name="chain_child",
        default_parameters={},
        paused=False,
    )
    pool_id = dep.get("work_pool_id") or "default-process-pool"

    stop = threading.Event()
    threads: list[threading.Thread] = []
    for idx in range(3):
        thread = threading.Thread(
            target=run_worker_loop,
            kwargs={
                "control_plane": plane,
                "worker_name": f"subflow-test-worker-{idx}",
                "work_pool_id": pool_id,
                "flow_registry": registry,
                "lease_seconds": 30,
                "stop_event": stop,
            },
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    try:
        assert parent_flow() == 3
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=10)
