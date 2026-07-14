"""Concurrent ``task.submit()`` via ThreadPoolTaskRunner."""

from __future__ import annotations

import threading
import time

import pytest

from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task, wait
from prefect_compat.task_runners import SequentialTaskRunner, ThreadPoolTaskRunner


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


def test_submit_failure_surfaces_on_result(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "fail.jsonl"))
    set_control_plane(plane)

    @task
    def boom() -> None:
        raise ValueError("submit boom")

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=2))
    def f() -> None:
        fut = boom.submit()
        with pytest.raises(ValueError, match="submit boom"):
            fut.result()

    f()


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
    from prefect_compat.runtime import RunState

    tasks = [t for t in plane._tasks.values() if t.task_name == "inc"]
    assert len(tasks) == 16
    assert all(t.state == RunState.COMPLETED for t in tasks)
    assert all(t.version >= 3 for t in tasks)  # scheduled → pending → running → completed
