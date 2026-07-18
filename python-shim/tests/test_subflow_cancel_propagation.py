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


def _cancel_parent_when_ready(
    plane: InMemoryControlPlane,
    parent_id_holder: list[UUID | None],
    *,
    dep_run_id_holder: list[str] | None = None,
    require_task_id_holder: list[str] | None = None,
    require_active_deployment: bool = False,
    timeout_seconds: float = 5.0,
) -> None:
    """Cancel once the parent (and optional child markers) exist; retry FSM races."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        parent_id = parent_id_holder[0] if parent_id_holder else None
        if parent_id is None:
            time.sleep(0.01)
            continue
        if require_task_id_holder is not None and not require_task_id_holder:
            time.sleep(0.01)
            continue
        if dep_run_id_holder is not None and not dep_run_id_holder:
            time.sleep(0.01)
            continue
        if require_active_deployment and dep_run_id_holder:
            dr = plane.get_deployment_run(UUID(dep_run_id_holder[0]))
            status = str(dr.get("status")) if dr else ""
            if status not in {"SCHEDULED", "CLAIMED", "RUNNING"}:
                time.sleep(0.01)
                continue
        try:
            plane.cancel_flow_run(parent_id)
            return
        except ValueError:
            # Parent may still be mid PENDING→RUNNING under the Rust FSM.
            time.sleep(0.01)


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

        fut = deployment_ref("child-deploy").submit(detach=True)
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

    threading.Thread(
        target=_cancel_parent_when_ready,
        args=(plane, parent_run_id),
        kwargs={
            "dep_run_id_holder": dep_run_id,
            "require_active_deployment": True,
        },
        daemon=True,
    ).start()
    try:
        with pytest.raises((RuntimeError, FlowRunCancelled), match="cancelled"):
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
        # Wait until the nested leaf run exists so cancel is not racing the
        # initial PENDING→RUNNING transition (version conflict).
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if parent_id and parent_id[0] is not None:
                if any(f.name == "leaf" for f in plane._flows.values()):
                    plane.cancel_flow_run(parent_id[0])
                    return
            time.sleep(0.01)

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

    threading.Thread(
        target=_cancel_parent_when_ready,
        args=(plane, parent_id),
        kwargs={
            "require_task_id_holder": task_id,
            "require_active_deployment": False,
        },
        daemon=True,
    ).start()
    try:
        with pytest.raises((RuntimeError, FlowRunCancelled), match="cancelled"):
            parent_flow()

        # Mirror onto the surrogate task can lag slightly; wait while the
        # worker is still alive so cancel propagation can finish.
        deadline = time.monotonic() + 5.0
        task = plane.get_task_run(UUID(task_id[0]))
        while time.monotonic() < deadline and task.state != RunState.CANCELLED:
            time.sleep(0.05)
            task = plane.get_task_run(UUID(task_id[0]))
        assert task.kind == "subflow"
        assert task.state == RunState.CANCELLED
    finally:
        stop.set()
        worker.join(timeout=5)
