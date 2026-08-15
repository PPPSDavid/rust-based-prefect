"""Airtight concurrent-state invariants.

These tests fail on illegal transitions, double claims, leaked GCL slots, and
incorrect wait_all aggregation. They are not a perf_matrix latency gate.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from prefect_compat import (
    InMemoryControlPlane,
    RunState,
    ThreadPoolTaskRunner,
    concurrency,
    create_concurrency_limit,
    flow,
    get_run_context,
    set_control_plane,
    task,
    wait,
)
from prefect_compat.errors import FlowChildrenFailed
from prefect_compat.runtime import FlowRunRecord

pytestmark = pytest.mark.airtight


def _plane(tmp_path: Path, name: str = "airtight") -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / f"{name}.jsonl"))


def _start_flow(plane: InMemoryControlPlane, name: str) -> FlowRunRecord:
    run = plane.create_flow_run(name)
    plane.set_flow_state(run.run_id, RunState.PENDING, uuid4(), "propose", expected_version=0)
    plane.set_flow_state(run.run_id, RunState.RUNNING, uuid4(), "start", expected_version=1)
    return plane.get_flow(run.run_id)


def test_parallel_distinct_flow_runs_legal_terminals(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "parallel-flows")
    set_control_plane(plane)

    @task
    def add(x: int) -> int:
        return x + 1

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def f(n: int) -> int:
        a = add.submit(n)
        b = add.submit(n + 1)
        wait([a, b])
        return a.result() + b.result()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(f, range(8)))
    assert results == [2 * i + 3 for i in range(8)]

    page = plane.list_flow_runs(limit=50)
    flow_rows = [row for row in page.items if row["name"] == "f"]
    assert len(flow_rows) == 8
    for row in flow_rows:
        assert row["state"] == "COMPLETED"
        assert int(row["version"]) >= 2

    applied = [event for event in plane.events() if event.get("kind") or event.get("event_type")]
    assert len(applied) >= 8 * 2


def test_wait_all_failed_submit_cannot_complete_under_overlap(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "wait-all-fail")
    set_control_plane(plane)

    @task
    def boom() -> None:
        raise ValueError("overlap boom")

    @task
    def ok() -> int:
        return 1

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def f() -> str:
        boom.submit()
        ok.submit()
        return "ok"

    with pytest.raises(FlowChildrenFailed):
        f()
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.FAILED


def test_wait_all_detach_failed_stays_completed(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "wait-all-detach")
    set_control_plane(plane)

    @task
    def boom() -> None:
        raise ValueError("detached")

    @task
    def ok() -> int:
        return 1

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def f() -> int:
        boom.submit(detach=True)
        return ok.submit().result()

    assert f() == 1
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.COMPLETED


def test_late_completed_after_cancel_stays_cancelled(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "late-complete")
    run = _start_flow(plane, "fence")
    task_run = plane.create_task_run(run.run_id, "work")
    plane.record_task_event(task_run.task_run_id, "task_pending", None)
    plane.record_task_event(task_run.task_run_id, "task_running", None)
    plane.cancel_flow_run(run.run_id)
    plane.record_task_event(task_run.task_run_id, "task_completed", {"late": True})
    assert plane.get_task_run(task_run.task_run_id).state == RunState.CANCELLED
    assert plane.get_flow(run.run_id).state == RunState.CANCELLED


def test_concurrent_claims_exactly_one_winner(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "claim-race")
    dep = plane.create_deployment(
        name="claim-race-dep",
        flow_name="simple_flow",
        default_parameters={"n": 1},
        paused=False,
    )
    plane.trigger_deployment_run(UUID(str(dep["id"])))

    def claim(worker: str) -> str:
        claimed = plane.claim_next_deployment_run(worker, lease_seconds=30)
        return "claimed" if claimed is not None else "empty"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(claim, [f"w-{i}" for i in range(8)]))
    assert outcomes.count("claimed") == 1
    assert outcomes.count("empty") == 7


def test_concurrency_context_binds_task_holder(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "gcl-bind")
    set_control_plane(plane)
    create_concurrency_limit("db", 1, plane=plane)

    @task
    def hold() -> str:
        with concurrency("db", occupy=1, lease_duration=60):
            ctx = get_run_context()
            assert ctx.task_run_id is not None
            lim = plane.get_concurrency_limit("db")
            assert lim is not None
            assert int(lim["active_slots"]) == 1
            rows = plane._query_rows(
                "SELECT holder_id FROM concurrency_leases WHERE holder_id = ?",
                [str(ctx.task_run_id)],
            )
            assert len(rows) == 1
            return "ok"

    @flow
    def f() -> str:
        return hold.submit().result()

    assert f() == "ok"
    lim = plane.get_concurrency_limit("db")
    assert lim is not None
    assert int(lim["active_slots"]) == 0


def test_cancel_releases_gcl_leases_by_holder(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "gcl-cancel")
    set_control_plane(plane)
    create_concurrency_limit("db", 1, plane=plane)
    run = _start_flow(plane, "holder")
    task_run = plane.create_task_run(run.run_id, "hold")
    acquired = plane.acquire_concurrency_slots(
        ["db"],
        occupy=1,
        lease_duration=60,
        holder_type="task_run",
        holder_id=str(task_run.task_run_id),
    )
    assert acquired["status"] == "acquired"
    lim = plane.get_concurrency_limit("db")
    assert lim is not None
    assert int(lim["active_slots"]) == 1

    plane.cancel_flow_run(run.run_id)
    lim = plane.get_concurrency_limit("db")
    assert lim is not None
    assert int(lim["active_slots"]) == 0
    blocked = plane.acquire_concurrency_slots(["db"], occupy=1, lease_duration=60)
    assert blocked["status"] == "acquired"


def test_terminate_pause_releases_gcl_leases(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "gcl-pause")
    set_control_plane(plane)
    create_concurrency_limit("db", 1, plane=plane)
    run = _start_flow(plane, "pause-hold")
    task_run = plane.create_task_run(run.run_id, "hold")
    plane.record_task_event(task_run.task_run_id, "task_pending", None)
    plane.record_task_event(task_run.task_run_id, "task_running", None)
    acquired = plane.acquire_concurrency_slots(
        ["db"],
        occupy=1,
        lease_duration=60,
        holder_type="task_run",
        holder_id=str(task_run.task_run_id),
    )
    assert acquired["status"] == "acquired"
    plane.pause_flow_run(run.run_id, mode="terminate")
    lim = plane.get_concurrency_limit("db")
    assert lim is not None
    assert int(lim["active_slots"]) == 0
