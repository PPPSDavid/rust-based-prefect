"""Case-study coverage for deferred concurrent ``submit`` semantics.

Representative constructions:
- diamond wait_for graph
- wide independent submits
- failing upstream → dependent cancelled (never RUNNING)
- tagged slot gating under ThreadPool
- Rust FSM markers on every transition
- process-pool picklable submit (non-Windows)
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from uuid import UUID

import pytest

from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task, wait
from prefect_compat.concurrency import create_tag_concurrency_limit
from prefect_compat.hooks import on_transition
from prefect_compat.mp_picklable import inc as _mp_inc
from prefect_compat.runtime import RunState
from prefect_compat.task_runners import (
    ProcessPoolTaskRunner,
    ThreadPoolTaskRunner,
)

_picklable_inc = task(_mp_inc)


def _task_event_seq(plane: InMemoryControlPlane, task_run_id: str) -> list[str]:
    return [
        str(e["event_type"])
        for e in plane._events
        if e.get("task_run_id") == task_run_id
    ]


def _assert_rust_from_to(plane: InMemoryControlPlane) -> None:
    """When the native lib is loaded, every task event must carry Rust from/to markers.

    CI ``python-rust`` may run pytest before ``cargo build``; skip the hot-path
    markers in that mode (behavior under Python fallback is still asserted).
    """
    if not plane._rust_fsm_active():
        return
    for ev in plane._events:
        if not str(ev.get("event_type", "")).startswith("task_"):
            continue
        assert ev.get("from_state"), f"missing from_state on {ev}"
        assert ev.get("to_state"), f"missing to_state on {ev}"


def test_case_study_diamond_wait_for_state_sequence(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "diamond.jsonl"))
    set_control_plane(plane)
    started: list[str] = []
    lock = threading.Lock()

    @task
    def node(name: str, ms: int = 50) -> str:
        with lock:
            started.append(name)
        time.sleep(ms / 1000.0)
        return name

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def diamond() -> str:
        a = node.submit("A", 80)
        b = node.submit("B", 40, wait_for=[a])
        c = node.submit("C", 40, wait_for=[a])
        d = node.submit("D", 20, wait_for=[b, c])
        wait([a, b, c, d])
        return d.result()

    assert diamond() == "D"
    _assert_rust_from_to(plane)

    by_name: dict[str, list[str]] = {}
    for t in plane._tasks.values():
        by_name[t.task_name] = _task_event_seq(plane, str(t.task_run_id))
        assert t.state == RunState.COMPLETED
        assert by_name[t.task_name] == [
            "task_pending",
            "task_running",
            "task_completed",
        ]
    # All tasks share name "node" — count instead.
    tasks = list(plane._tasks.values())
    assert len(tasks) == 4
    assert all(t.state == RunState.COMPLETED for t in tasks)
    assert started[0] == "A"
    assert set(started[1:3]) == {"B", "C"}
    assert started[-1] == "D"


def test_case_study_wide_independent_submits_no_early_completed(tmp_path):
    """Fan-out submit must not mark COMPLETED before bodies finish."""
    plane = InMemoryControlPlane(history_path=str(tmp_path / "wide.jsonl"))
    set_control_plane(plane)
    release = threading.Event()

    @task
    def work(i: int) -> int:
        release.wait(timeout=5)
        return i

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=8))
    def wide() -> int:
        futs = [work.submit(i) for i in range(8)]
        states = [
            plane.get_task_run(UUID(f.task_run_id)).state
            for f in futs
            if f.task_run_id
        ]
        # Bodies are held; COMPLETED must not appear yet. RUNNING is OK once promoted.
        assert all(s in (RunState.PENDING, RunState.RUNNING) for s in states)
        assert RunState.COMPLETED not in states
        release.set()
        wait(futs)
        return sum(f.result() for f in futs)

    assert wide() == sum(range(8))
    _assert_rust_from_to(plane)
    for t in plane._tasks.values():
        if t.task_name != "work":
            continue
        assert _task_event_seq(plane, str(t.task_run_id)) == [
            "task_pending",
            "task_running",
            "task_completed",
        ]


def test_case_study_upstream_failure_cancels_deferred_dependent(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "fail_dep.jsonl"))
    set_control_plane(plane)

    @task
    def boom() -> None:
        raise ValueError("upstream boom")

    @task
    def dependent() -> int:
        return 1

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def f() -> None:
        a = boom.submit()
        b = dependent.submit(wait_for=[a])
        with pytest.raises(ValueError, match="upstream boom"):
            a.result()
        with pytest.raises(ValueError, match="upstream boom"):
            b.result()
        assert a.task_run_id and b.task_run_id
        assert plane.get_task_run(UUID(a.task_run_id)).state == RunState.FAILED
        assert plane.get_task_run(UUID(b.task_run_id)).state == RunState.CANCELLED
        assert _task_event_seq(plane, b.task_run_id) == [
            "task_pending",
            "task_cancelled",
        ]

    f()
    _assert_rust_from_to(plane)


def test_case_study_tagged_submit_caps_running_and_uses_rust_gcl(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "tagged.jsonl"))
    set_control_plane(plane)
    create_tag_concurrency_limit("case", 1, plane=plane)
    # When Rust is loaded, prove GCL ops are native-backed (not Python fallback).
    if plane._rust_fsm_active():
        listed = plane._rust_fsm_call("gcl_list", {})
        assert listed.get("ok") is True
        names = {str(x.get("name")) for x in (listed.get("limits") or [])}
        assert "tag:case" in names

    in_body = 0
    max_in_body = 0
    lock = threading.Lock()

    @task(tags=["case"])
    def capped(ms: int) -> int:
        nonlocal in_body, max_in_body
        with lock:
            in_body += 1
            max_in_body = max(max_in_body, in_body)
        try:
            time.sleep(ms / 1000.0)
        finally:
            with lock:
                in_body -= 1
        return ms

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def f() -> None:
        futs = [capped.submit(80) for _ in range(4)]
        wait(futs)

    f()
    assert max_in_body == 1
    for t in plane._tasks.values():
        if t.task_name != "capped":
            continue
        assert t.state == RunState.COMPLETED
        assert _task_event_seq(plane, str(t.task_run_id)) == [
            "task_pending",
            "task_running",
            "task_completed",
        ]
    _assert_rust_from_to(plane)


def test_case_study_deferred_hooks_fire_on_pending_and_running(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "hooks.jsonl"))
    set_control_plane(plane)
    edges: list[tuple[str, str]] = []

    @task(
        transition_hooks=[
            on_transition(
                lambda c: edges.append((c.from_state.value, c.to_state.value))
            )
        ]
    )
    def inc(x: int) -> int:
        return x + 1

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=2))
    def f() -> int:
        a = inc.submit(1)
        b = inc.submit(2, wait_for=[a])
        wait([a, b])
        return b.result()

    assert f() == 3
    # Two tasks × (SCHEDULED→PENDING, PENDING→RUNNING, RUNNING→COMPLETED)
    assert edges.count(("SCHEDULED", "PENDING")) == 2
    assert edges.count(("PENDING", "RUNNING")) == 2
    assert edges.count(("RUNNING", "COMPLETED")) == 2


@pytest.mark.skipif(
    os.name == "nt",
    reason="multiprocessing pool from pytest is unreliable on Windows spawn",
)
def test_case_study_process_pool_submit_state_sequence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plane = InMemoryControlPlane(history_path=str(tmp_path / "proc.jsonl"))
    set_control_plane(plane)

    @flow(task_runner=ProcessPoolTaskRunner(max_workers=2))
    def f() -> int:
        a = _picklable_inc.submit(1)
        b = _picklable_inc.submit(2, wait_for=[a])
        wait([a, b])
        return a.result() + b.result()

    assert f() == 1 + 1 + 2 + 1
    _assert_rust_from_to(plane)
    for t in plane._tasks.values():
        if t.task_name != "inc":
            continue
        assert _task_event_seq(plane, str(t.task_run_id)) == [
            "task_pending",
            "task_running",
            "task_completed",
        ]


def test_case_study_server_demo_flows_state_integrity(tmp_path):
    """Exercise the same shapes exposed via /benchmark/run for UI seeding."""
    from prefect_compat.server import failing_flow, long_chain_flow, simple_flow, wide_flow

    plane = InMemoryControlPlane(history_path=str(tmp_path / "demo.jsonl"))
    set_control_plane(plane)

    assert simple_flow(3) == 8  # (3+1)*2
    assert wide_flow(4) == sum(range(4)) * 2
    assert long_chain_flow(3) == 3

    with pytest.raises(RuntimeError, match="intentional failure"):
        failing_flow(2)

    _assert_rust_from_to(plane)

    # Failing flow: explode FAILED; after_failure never runs → CANCELLED under deferred wait.
    by_name: dict[str, list[RunState]] = defaultdict(list)
    for t in plane._tasks.values():
        by_name[t.task_name].append(t.state)
    assert RunState.FAILED in by_name["explode"]
    assert RunState.CANCELLED in by_name["after_failure"]
