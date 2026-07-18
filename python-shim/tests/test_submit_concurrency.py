"""Concurrent ``task.submit()`` via ThreadPool / ProcessPool task runners."""

from __future__ import annotations

import os
import threading
import time
from uuid import UUID

import pytest

from prefect_compat import InMemoryControlPlane, RunState, flow, set_control_plane, task, wait
from prefect_compat.concurrency import create_tag_concurrency_limit
from prefect_compat.mp_picklable import sleep_ms as _mp_sleep_ms
from prefect_compat.task_runners import (
    ProcessPoolTaskRunner,
    SequentialTaskRunner,
    ThreadPoolTaskRunner,
)

_picklable_sleep = task(_mp_sleep_ms)


def test_independent_submits_overlap_with_thread_pool(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "overlap.jsonl"))
    set_control_plane(plane)
    in_sleep = 0
    max_concurrent = 0
    lock = threading.Lock()

    @task
    def sleep_ms(ms: int) -> int:
        nonlocal in_sleep, max_concurrent
        with lock:
            in_sleep += 1
            max_concurrent = max(max_concurrent, in_sleep)
        try:
            time.sleep(ms / 1000.0)
        finally:
            with lock:
                in_sleep -= 1
        return ms

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def f() -> None:
        a = sleep_ms.submit(250)
        b = sleep_ms.submit(250)
        wait([a, b])

    f()
    # Prefer a concurrency counter over a brittle wall-clock ceiling (CI measured
    # ~0.434s once vs a <0.4 bound while still overlapping).
    assert max_concurrent >= 2, f"expected overlapping submits, max_concurrent={max_concurrent}"


def test_wait_for_gates_submit_body_start(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "gate.jsonl"))
    set_control_plane(plane)
    started: list[str] = []

    @task
    def sleep_tagged(tag: str, ms: int) -> str:
        started.append(tag)
        time.sleep(ms / 1000.0)
        return tag

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def f() -> None:
        a = sleep_tagged.submit("upstream", 200)
        b = sleep_tagged.submit("downstream", 50, wait_for=[a])
        wait([a, b])

    t0 = time.perf_counter()
    f()
    elapsed = time.perf_counter() - t0
    assert started == ["upstream", "downstream"]
    # Upstream 0.2s + downstream 0.05s must not overlap.
    assert elapsed >= 0.22, f"expected gated serialization, wall={elapsed:.3f}s"


def test_wait_for_does_not_block_submit_return(tmp_path):
    """wait_for must gate the worker body, not the coordinating-thread submit return."""
    plane = InMemoryControlPlane(history_path=str(tmp_path / "wait_return.jsonl"))
    set_control_plane(plane)

    @task
    def sleep_ms(ms: int) -> int:
        time.sleep(ms / 1000.0)
        return ms

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def f() -> float:
        a = sleep_ms.submit(200)
        t0 = time.perf_counter()
        b = sleep_ms.submit(50, wait_for=[a])
        submit_elapsed = time.perf_counter() - t0
        wait([a, b])
        return submit_elapsed

    assert f() < 0.1, "submit(..., wait_for=...) must return before upstream finishes"


def test_submit_stays_pending_until_worker_promotes(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "pending.jsonl"))
    set_control_plane(plane)
    release = threading.Event()
    upstream_started = threading.Event()

    @task
    def hold() -> int:
        upstream_started.set()
        release.wait(timeout=5)
        return 1

    @task
    def dependent() -> int:
        return 2

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def f() -> None:
        a = hold.submit()
        assert upstream_started.wait(timeout=2)
        b = dependent.submit(wait_for=[a])
        assert b.task_run_id is not None
        tr = plane.get_task_run(UUID(b.task_run_id))
        assert tr.state == RunState.PENDING
        release.set()
        wait([a, b])

    f()


def test_tagged_submit_returns_before_slot_acquire(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "tag_return.jsonl"))
    set_control_plane(plane)
    create_tag_concurrency_limit("db", 1, plane=plane)

    @task(tags=["db"])
    def sleeper(ms: int) -> int:
        time.sleep(ms / 1000.0)
        return ms

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def f() -> float:
        a = sleeper.submit(250)
        t0 = time.perf_counter()
        b = sleeper.submit(50)
        submit_elapsed = time.perf_counter() - t0
        wait([a, b])
        return submit_elapsed

    assert f() < 0.1, "tagged submit must not block on slot acquire on the caller"


def test_submit_failure_surfaces_on_result(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "fail.jsonl"))
    set_control_plane(plane)

    @task
    def boom() -> None:
        raise ValueError("submit boom")

    @flow(
        final_state="explicit",
        task_runner=ThreadPoolTaskRunner(max_workers=2),
    )
    def f() -> None:
        fut = boom.submit()
        with pytest.raises(ValueError, match="submit boom"):
            fut.result()

    f()
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.COMPLETED


def test_sequential_runner_submit_does_not_overlap(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "seq.jsonl"))
    set_control_plane(plane)

    @task
    def sleep_ms(ms: int) -> int:
        time.sleep(ms / 1000.0)
        return ms

    @flow(task_runner=SequentialTaskRunner())
    def f() -> None:
        a = sleep_ms.submit(150)
        b = sleep_ms.submit(150)
        wait([a, b])

    t0 = time.perf_counter()
    f()
    elapsed = time.perf_counter() - t0
    assert elapsed >= 0.28, f"sequential submit should not overlap, wall={elapsed:.3f}s"


def test_concurrent_submit_control_plane_uses_rust_fsm_when_available(tmp_path):
    """Prepare + COMPLETED must stay on the locked Rust FSM path under thread concurrency."""
    plane = InMemoryControlPlane(history_path=str(tmp_path / "rust.jsonl"))
    set_control_plane(plane)
    if not plane._rust_fsm_active():
        pytest.skip("native rust FSM not loaded")  # ty: ignore[too-many-positional-arguments]

    @task
    def inc(x: int) -> int:
        return x + 1

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=8))
    def f() -> int:
        futs = [inc.submit(i) for i in range(16)]
        wait(futs)
        return sum(fut.result() for fut in futs)

    assert f() == sum(i + 1 for i in range(16))
    # All task runs reached COMPLETED through control-plane transitions (Rust-backed).
    tasks = [t for t in plane._tasks.values() if t.task_name == "inc"]
    assert len(tasks) == 16
    assert all(t.state == RunState.COMPLETED for t in tasks)
    assert all(t.version >= 3 for t in tasks)  # scheduled → pending → running → completed


@pytest.mark.skipif(
    os.name == "nt",
    reason="multiprocessing pool from pytest is unreliable on Windows spawn",
)
def test_independent_submits_overlap_with_process_pool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plane = InMemoryControlPlane(history_path=str(tmp_path / "proc_overlap.jsonl"))
    set_control_plane(plane)

    @flow(task_runner=ProcessPoolTaskRunner(max_workers=2))
    def f() -> float:
        t0 = time.perf_counter()
        a = _picklable_sleep.submit(200)
        b = _picklable_sleep.submit(200)
        wait([a, b])
        return time.perf_counter() - t0

    # Two 0.2s process sleeps should overlap under the process pool.
    assert f() < 0.35, "process-pool submit should overlap independent bodies"
