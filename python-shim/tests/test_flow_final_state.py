"""Flow-run final state resolution (wait_all default + detach / explicit)."""

from __future__ import annotations

from pathlib import Path

import pytest
from prefect_compat import (
    InMemoryControlPlane,
    RunState,
    ThreadPoolTaskRunner,
    deployment_ref,
    flow,
    set_control_plane,
    task,
)
from prefect_compat.errors import FlowChildrenFailed
from prefect_compat.worker import run_local_deployment_once


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / "hist.jsonl"))


def test_wait_all_unobserved_failed_submit_fails_flow(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @task
    def boom() -> None:
        raise ValueError("submit boom")

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=2))
    def f() -> str:
        boom.submit()  # never .result()
        return "ok"

    with pytest.raises(FlowChildrenFailed):
        f()

    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.FAILED


def test_wait_all_all_tasks_complete(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @task
    def add(x: int) -> int:
        return x + 1

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=2))
    def f() -> int:
        a = add.submit(1)
        b = add.submit(2)
        return a.result() + b.result()

    assert f() == 5
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.COMPLETED


def test_detach_failed_task_does_not_fail_flow(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @task
    def boom() -> None:
        raise ValueError("detached boom")

    @task
    def ok() -> int:
        return 1

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=2))
    def f() -> int:
        boom.submit(detach=True)
        return ok.submit().result()

    assert f() == 1
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.COMPLETED


def test_explicit_mode_allows_unobserved_failure(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @task
    def boom() -> None:
        raise ValueError("explicit boom")

    @flow(final_state="explicit", task_runner=ThreadPoolTaskRunner(max_workers=2))
    def f() -> str:
        boom.submit()
        return "ok"

    assert f() == "ok"
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.COMPLETED


def test_explicit_fire_and_forget_subflow(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}

    @flow
    def child_flow(n: int) -> int:
        return n

    @flow(final_state="explicit")
    def parent_flow() -> str:
        deployment_ref("child-deploy").submit(n=1)
        return "ok"

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow
    plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    assert parent_flow() == "ok"
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.COMPLETED


def test_wait_all_waits_for_deployment_subflow(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}
    seen: list[str] = []

    @flow
    def child_flow(n: int) -> int:
        seen.append("child")
        return n * 2

    @flow
    def parent_flow() -> str:
        # No .result() — wait_all must still wait and succeed.
        deployment_ref("child-deploy").submit(n=3)
        seen.append("parent_body")
        return "ok"

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow
    plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )

    import threading

    stop = threading.Event()

    def worker() -> None:
        while not stop.is_set():
            run_local_deployment_once(plane, "w1", "default-process-pool", registry)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        assert parent_flow() == "ok"
        assert "child" in seen
        parents = [
            r
            for r in plane.list_flow_runs(limit=20).items
            if r.get("name") == "parent_flow"
        ]
        assert parents
        assert parents[0]["state"] == RunState.COMPLETED.value
    finally:
        stop.set()
        t.join(timeout=5)


def test_detach_subflow_does_not_block(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    registry: dict = {}

    @flow
    def child_flow(n: int) -> int:
        return n

    @flow
    def parent_flow() -> str:
        deployment_ref("child-deploy").submit(n=1, detach=True)
        return "ok"

    registry["child_flow"] = child_flow
    registry["parent_flow"] = parent_flow
    plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={},
        paused=False,
    )
    assert parent_flow() == "ok"
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.COMPLETED
