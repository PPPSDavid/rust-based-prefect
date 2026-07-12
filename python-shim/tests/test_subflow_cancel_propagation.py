"""Phase 3: parent cancel propagates to deployment and nested subflows."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from uuid import UUID

import pytest

from prefect_compat import InMemoryControlPlane, deployment_ref, flow, set_control_plane
from prefect_compat.cancellation import FlowRunCancelled, sleep_cancelable
from prefect_compat.runtime import RunState
from prefect_compat.worker import run_worker_loop


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / "subflow-cancel.jsonl"))


def _start_worker(
    plane: InMemoryControlPlane, registry: dict
) -> tuple[threading.Event, threading.Thread]:
    stop = threading.Event()
    thread = threading.Thread(
        target=run_worker_loop,
        kwargs={
            "control_plane": plane,
            "worker_name": "cancel-test-worker",
            "work_pool_id": "default-process-pool",
            "flow_registry": registry,
            "lease_seconds": 30,
            "stop_event": stop,
        },
        daemon=True,
    )
    thread.start()
    return stop, thread


def test_cancel_parent_cancels_scheduled_deployment_subflow(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}
    dep_run_id: list[str] = []

    @flow
    def child_flow() -> int:
        return 1

    @flow
    def parent_flow() -> None:
        from prefect_compat.decorators import _ACTIVE_FLOW_RUN

        fut = deployment_ref("child-deploy").submit()
        dep_run_id.append(fut.deployment_run_id)
        # fire-and-forget — parent completes without waiting
        _ = _ACTIVE_FLOW_RUN.get()

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow

    plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    parent_runs = []

    @flow
    def launcher() -> None:
        parent_flow()
        parent_runs.append(plane.latest_flow())

    registry["launcher"] = launcher
    launcher()

    parent_run = parent_runs[0]
    assert parent_run is not None
    plane.cancel_flow_run(parent_run.run_id)

    dr = plane.get_deployment_run(UUID(dep_run_id[0]))
    assert dr is not None
    assert dr["status"] == "CANCELLED"


def test_cancel_parent_cancels_running_deployment_subflow(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}
    dep_run_id: list[str] = []
    parent_run_id: list[UUID | None] = []

    @flow
    def child_flow() -> None:
        sleep_cancelable(5.0, poll_seconds=0.05)

    @flow
    def parent_flow() -> None:
        from prefect_compat.decorators import _ACTIVE_FLOW_RUN

        parent_run_id.append(_ACTIVE_FLOW_RUN.get())
        fut = deployment_ref("child-deploy").submit()
        dep_run_id.append(fut.deployment_run_id)
        fut.result()

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow

    plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    stop, worker = _start_worker(plane, registry)

    def cancel_parent() -> None:
        time.sleep(0.2)
        if parent_run_id and parent_run_id[0] is not None:
            plane.cancel_flow_run(parent_run_id[0])

    threading.Thread(target=cancel_parent, daemon=True).start()
    try:
        with pytest.raises(RuntimeError, match="cancelled"):
            parent_flow()
    finally:
        stop.set()
        worker.join(timeout=5)

    dr = plane.get_deployment_run(UUID(dep_run_id[0]))
    assert dr is not None
    assert dr["status"] == "CANCELLED"


def test_cancel_nested_inline_grandchild(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    parent_id: list[UUID | None] = []

    @flow
    def leaf() -> None:
        sleep_cancelable(5.0, poll_seconds=0.05)

    @flow
    def mid() -> None:
        leaf()

    @flow
    def root() -> None:
        from prefect_compat.decorators import _ACTIVE_FLOW_RUN

        parent_id.append(_ACTIVE_FLOW_RUN.get())
        mid()

    def cancel_root() -> None:
        time.sleep(0.15)
        if parent_id and parent_id[0] is not None:
            plane.cancel_flow_run(parent_id[0])

    threading.Thread(target=cancel_root, daemon=True).start()
    with pytest.raises(FlowRunCancelled):
        root()

    leaf_run = next(f for f in plane._flows.values() if f.name == "leaf")
    mid_run = next(f for f in plane._flows.values() if f.name == "mid")
    assert mid_run.state == RunState.CANCELLED
    assert leaf_run.state == RunState.CANCELLED


def test_cancel_mirrors_surrogate_subflow_task(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}
    task_id: list[str] = []
    parent_id: list[UUID | None] = []

    @flow
    def child_flow() -> int:
        sleep_cancelable(5.0, poll_seconds=0.05)
        return 1

    @flow
    def parent_flow() -> None:
        from prefect_compat.decorators import _ACTIVE_FLOW_RUN

        parent_id.append(_ACTIVE_FLOW_RUN.get())
        fut = deployment_ref("child-deploy").submit()
        task_id.append(fut.parent_task_run_id)
        fut.result()

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow

    plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    stop, worker = _start_worker(plane, registry)

    def cancel_parent() -> None:
        time.sleep(0.2)
        if parent_id and parent_id[0] is not None:
            plane.cancel_flow_run(parent_id[0])

    threading.Thread(target=cancel_parent, daemon=True).start()
    try:
        with pytest.raises(RuntimeError):
            parent_flow()
    finally:
        stop.set()
        worker.join(timeout=5)

    task = plane.get_task_run(UUID(task_id[0]))
    assert task.kind == "subflow"
    assert task.state == RunState.CANCELLED
