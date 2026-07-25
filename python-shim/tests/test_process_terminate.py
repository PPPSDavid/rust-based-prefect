"""P3.2c/d: process-kill terminate + hard-pause resume via P1 lineage."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from prefect_compat import (
    ProcessPoolTaskRunner,
    flow,
    set_control_plane,
    task,
)
from prefect_compat.mp_picklable import blind_sleep, return_none
from prefect_compat.process_workers import task_process_registry
from prefect_compat.runtime import InMemoryControlPlane, RunState

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="process terminate signals are POSIX-oriented in this slice",
)


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / "proc-term.jsonl"))


def _wait_for_registered_worker(
    plane: InMemoryControlPlane, *, timeout: float = 10.0
) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = plane.latest_flow()
        if run is not None:
            workers = task_process_registry().snapshot_for_flow(run.run_id)
            if workers:
                return run
        time.sleep(0.05)
    raise AssertionError("expected a registered child process before timeout")


def test_cancel_kills_blind_sleep_process(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    sleepy = task(blind_sleep)

    @flow(task_runner=ProcessPoolTaskRunner(max_workers=1))
    def f() -> str:
        return sleepy.submit(60.0).result()

    def _run() -> None:
        try:
            f()
        except Exception:
            return

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    run = _wait_for_registered_worker(plane)
    workers = task_process_registry().snapshot_for_flow(run.run_id)
    assert workers, "expected a registered child process"
    pid = workers[0].process.pid
    assert pid is not None

    detail = plane.cancel_flow_run(run.run_id)
    assert detail["state"] == "CANCELLED"
    assert detail.get("terminated_task_run_ids")

    # Child should be dead shortly after cancel.
    for _ in range(40):
        if not workers[0].process.is_alive():
            break
        time.sleep(0.05)
    assert not workers[0].process.is_alive()
    thread.join(timeout=5)


def test_terminate_pause_then_prepare_resume_reruns_interrupted(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    first = task(return_none, persist_result=True)
    sleepy = task(blind_sleep)

    @flow(name="term-resume", task_runner=ProcessPoolTaskRunner(max_workers=1))
    def pipeline() -> str:
        first.submit(1).result()
        return sleepy.submit(60.0).result()

    def _run() -> None:
        try:
            pipeline()
        except Exception:
            return

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    run = _wait_for_registered_worker(plane)
    assert run is not None

    paused = plane.pause_flow_run(run.run_id, mode="terminate")
    assert paused["state"] == "PAUSED"
    assert paused["interrupt_mode"] == "terminate"
    assert paused.get("terminated_task_run_ids")
    thread.join(timeout=5)

    # P3.2d: resume prepares P1 lineage; next invoke skips completed None, re-runs sleep.
    resumed = plane.resume_flow_run(run.run_id)
    assert resumed.get("resumed_via") == "prepare_resume"

    @flow(name="term-resume", task_runner=ProcessPoolTaskRunner(max_workers=1))
    def pipeline2() -> str:
        first.submit(1).result()
        # Short sleep on retry so the test finishes (different input → must recompute).
        return sleepy.submit(0.2).result()

    assert pipeline2() == "awake"
    new_run = plane.latest_flow()
    assert new_run is not None
    assert new_run.run_id != run.run_id
    assert new_run.state == RunState.COMPLETED
    # Cache hit on the None-returning first task.
    page = plane.list_task_runs(new_run.run_id, limit=50)
    first_rows = [t for t in page.items if t.get("task_name") == "return_none"]
    assert first_rows
    # Artifacts / event data may carry cache_hit; at minimum first task completed once.
    assert any(t.get("state") == "COMPLETED" for t in first_rows)
