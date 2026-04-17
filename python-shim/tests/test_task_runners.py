"""Task runner behavior for ``map``."""

from __future__ import annotations

import os

import pytest

from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task, wait
from prefect_compat.mp_picklable import inc as _mp_inc
from prefect_compat.task_runners import (
    ProcessPoolTaskRunner,
    SequentialTaskRunner,
    ThreadPoolTaskRunner,
)

# Task body must be a prefect_compat top-level function so ProcessPool workers unpickle reliably
# (pytest-loaded test modules are not stable import paths for multiprocessing).
_picklable_inc = task(_mp_inc)


def test_map_sequential_deterministic_order(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "seq.jsonl"))
    set_control_plane(plane)
    order: list[int] = []

    @task
    def mark(x: int) -> int:
        order.append(x)
        return x

    @flow(task_runner=SequentialTaskRunner())
    def f() -> list[int]:
        return [t.result() for t in mark.map([1, 2, 3])]

    assert f() == [1, 2, 3]
    assert order == [1, 2, 3]


def test_map_thread_pool_parallel_smoke(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "thread.jsonl"))
    set_control_plane(plane)

    @task
    def dbl(x: int) -> int:
        return x * 2

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def f() -> int:
        xs = dbl.map(range(8))
        wait(xs)
        return sum(t.result() for t in xs)

    assert f() == sum(i * 2 for i in range(8))


@pytest.mark.skipif(os.name == "nt", reason="multiprocessing pool from pytest is unreliable on Windows spawn")
def test_map_process_pool_picklable_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plane = InMemoryControlPlane(history_path=str(tmp_path / "proc.jsonl"))
    set_control_plane(plane)

    @flow(task_runner=ProcessPoolTaskRunner(max_workers=2))
    def f() -> int:
        xs = _picklable_inc.map([1, 2, 3])
        wait(xs)
        return sum(t.result() for t in xs)

    assert f() == 9


def test_default_runner_env_sequential(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONFLOW_TASK_RUNNER", "sequential")
    plane = InMemoryControlPlane(history_path=str(tmp_path / "envseq.jsonl"))
    set_control_plane(plane)
    seen: list[str] = []

    @task
    def tag(x: str) -> str:
        seen.append(x)
        return x

    @flow
    def f() -> list[str]:
        return [t.result() for t in tag.map(["a", "b"])]

    assert f() == ["a", "b"]
    assert seen == ["a", "b"]
