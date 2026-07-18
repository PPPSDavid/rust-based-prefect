"""Deferred submit × flow final_state × task runners (product matrix).

Goals under test:
- Concurrent runners: submit returns immediately; wait_all still sees children.
- ``final_state="wait_all"`` (default): contributing failures fail the flow.
- ``detach=True``: child is excluded from aggregation (concurrent runners).
- ``final_state="explicit"``: body return wins even if an unobserved child failed
  (concurrent runners only — sequential ``submit`` runs the body sync and raises).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from prefect_compat import (
    InMemoryControlPlane,
    RunState,
    flow,
    set_control_plane,
    task,
    wait,
)
from prefect_compat.errors import FlowChildrenFailed
from prefect_compat.mp_picklable import const_one, inc, raise_value_error
from prefect_compat.task_runners import (
    ProcessPoolTaskRunner,
    SequentialTaskRunner,
    ThreadPoolTaskRunner,
)

_inc = task(inc)
_ok = task(const_one)
_boom = task(raise_value_error)

_PROCESS_SKIP = pytest.mark.skipif(
    os.name == "nt",
    reason="multiprocessing pool from pytest is unreliable on Windows spawn",
)


def _plane(tmp_path: Path, name: str) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / f"{name}.jsonl"))


def _all_runners() -> list[Any]:
    return [
        pytest.param(SequentialTaskRunner(), id="sequential"),
        pytest.param(ThreadPoolTaskRunner(max_workers=2), id="thread"),
        pytest.param(
            ProcessPoolTaskRunner(max_workers=2),
            id="process",
            marks=_PROCESS_SKIP,
        ),
    ]


def _concurrent_runners() -> list[Any]:
    return [
        pytest.param(ThreadPoolTaskRunner(max_workers=2), id="thread"),
        pytest.param(
            ProcessPoolTaskRunner(max_workers=2),
            id="process",
            marks=_PROCESS_SKIP,
        ),
    ]


@pytest.mark.parametrize("runner", _all_runners())
@pytest.mark.parametrize("final_state", ["wait_all", "explicit"])
def test_successful_submits_complete_across_runners(
    tmp_path: Path, runner: Any, final_state: str
) -> None:
    plane = _plane(tmp_path, f"ok-{final_state}")
    set_control_plane(plane)

    @flow(final_state=final_state, task_runner=runner)
    def f() -> int:
        a = _inc.submit(1)
        b = _inc.submit(2)
        wait([a, b])
        return a.result() + b.result()

    assert f() == 5
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.COMPLETED
    for tr in plane._tasks.values():
        if tr.task_name == "inc":
            assert tr.contribute_to_flow_state is True
            assert tr.state == RunState.COMPLETED


@pytest.mark.parametrize("runner", _all_runners())
def test_wait_for_chain_succeeds_under_wait_all(tmp_path: Path, runner: Any) -> None:
    plane = _plane(tmp_path, "wait-for")
    set_control_plane(plane)

    @flow(task_runner=runner)
    def f() -> int:
        a = _inc.submit(1)
        b = _inc.submit(2, wait_for=[a])
        wait([a, b])
        return b.result()

    assert f() == 3
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.COMPLETED


@pytest.mark.parametrize("runner", _concurrent_runners())
def test_wait_all_unobserved_failure_fails_flow_concurrent(
    tmp_path: Path, runner: Any
) -> None:
    plane = _plane(tmp_path, "wait-all-fail")
    set_control_plane(plane)

    @flow(task_runner=runner)
    def f() -> str:
        _boom.submit()
        return "ok"

    with pytest.raises(FlowChildrenFailed):
        f()
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.FAILED


def test_wait_all_unobserved_failure_raises_on_sequential_submit(
    tmp_path: Path,
) -> None:
    """Sequential submit runs the body sync — failure surfaces at submit(), not wait_all."""
    plane = _plane(tmp_path, "seq-fail")
    set_control_plane(plane)

    @flow(task_runner=SequentialTaskRunner())
    def f() -> str:
        _boom.submit()
        return "ok"

    with pytest.raises(ValueError, match="submit boom"):
        f()
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.FAILED


@pytest.mark.parametrize("runner", _concurrent_runners())
def test_detach_failure_does_not_fail_wait_all_concurrent(
    tmp_path: Path, runner: Any
) -> None:
    plane = _plane(tmp_path, "detach")
    set_control_plane(plane)

    @flow(task_runner=runner)
    def f() -> int:
        boom_fut = _boom.submit(detach=True)
        assert boom_fut.task_run_id is not None
        tr = plane.get_task_run(UUID(boom_fut.task_run_id))
        assert tr.contribute_to_flow_state is False
        return _ok.submit().result()

    assert f() == 1
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.COMPLETED


def test_detach_does_not_suppress_sequential_submit_raise(tmp_path: Path) -> None:
    """detach only excludes aggregation; sequential body still raises on submit()."""
    plane = _plane(tmp_path, "seq-detach")
    set_control_plane(plane)

    @flow(task_runner=SequentialTaskRunner())
    def f() -> int:
        _boom.submit(detach=True)
        return 1

    with pytest.raises(ValueError, match="submit boom"):
        f()
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.FAILED
    boom_runs = [t for t in plane._tasks.values() if t.task_name == "raise_value_error"]
    assert boom_runs
    assert boom_runs[0].contribute_to_flow_state is False


@pytest.mark.parametrize("runner", _concurrent_runners())
def test_explicit_allows_unobserved_failure_concurrent(
    tmp_path: Path, runner: Any
) -> None:
    plane = _plane(tmp_path, "explicit")
    set_control_plane(plane)

    @flow(final_state="explicit", task_runner=runner)
    def f() -> str:
        _boom.submit()
        return "ok"

    assert f() == "ok"
    run = plane.latest_flow()
    assert run is not None
    assert run.state == RunState.COMPLETED


@pytest.mark.parametrize("runner", _concurrent_runners())
def test_detach_flag_set_on_pending_create_concurrent(
    tmp_path: Path, runner: Any
) -> None:
    """Deferred PENDING create must record contribute_to_flow_state before workers run."""
    plane = _plane(tmp_path, "flag")
    set_control_plane(plane)

    @flow(final_state="explicit", task_runner=runner)
    def f() -> None:
        attached = _ok.submit()
        detached = _ok.submit(detach=True)
        assert attached.task_run_id and detached.task_run_id
        assert (
            plane.get_task_run(UUID(attached.task_run_id)).contribute_to_flow_state
            is True
        )
        assert (
            plane.get_task_run(UUID(detached.task_run_id)).contribute_to_flow_state
            is False
        )
        wait([attached, detached])

    f()
