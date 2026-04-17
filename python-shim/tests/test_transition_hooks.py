"""Tests for unified (from_state, to_state) transition hooks."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from prefect_compat import (
    InMemoryControlPlane,
    RunState,
    TransitionContext,
    flow,
    on_transition,
    set_control_plane,
    task,
)
from prefect_compat.hooks import compile_transition_hooks, dispatch_transition_hooks


def test_dispatch_exact_and_wildcard_to_state() -> None:
    seen: list[tuple[RunState | None, RunState | None]] = []

    def record(ctx: TransitionContext) -> None:
        seen.append((ctx.from_state, ctx.to_state))

    specs = compile_transition_hooks(
        (
            on_transition(record, from_state=RunState.RUNNING, to_state=RunState.COMPLETED),
            on_transition(record, to_state=RunState.FAILED),
        )
    )
    assert specs is not None
    rid = uuid4()
    dispatch_transition_hooks(
        specs,
        TransitionContext(
            kind="flow",
            flow_run_id=rid,
            from_state=RunState.RUNNING,
            to_state=RunState.COMPLETED,
            transition_kind="complete",
        ),
    )
    assert seen == [(RunState.RUNNING, RunState.COMPLETED)]

    seen.clear()
    dispatch_transition_hooks(
        specs,
        TransitionContext(
            kind="flow",
            flow_run_id=rid,
            from_state=RunState.RUNNING,
            to_state=RunState.FAILED,
            transition_kind="fail",
        ),
    )
    assert seen == [(RunState.RUNNING, RunState.FAILED)]


def test_dispatch_no_hooks_is_noop() -> None:
    dispatch_transition_hooks(
        None,
        TransitionContext(
            kind="flow",
            flow_run_id=uuid4(),
            from_state=RunState.SCHEDULED,
            to_state=RunState.PENDING,
            transition_kind="propose",
        ),
    )


def test_hook_exception_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    def boom(ctx: TransitionContext) -> None:
        raise RuntimeError("hook oops")

    specs = compile_transition_hooks((on_transition(boom),))
    assert specs is not None
    with caplog.at_level(logging.ERROR, logger="prefect_compat.hooks"):
        dispatch_transition_hooks(
            specs,
            TransitionContext(
                kind="flow",
                flow_run_id=uuid4(),
                from_state=RunState.SCHEDULED,
                to_state=RunState.PENDING,
                transition_kind="propose",
            ),
        )
    assert "transition hook failed" in caplog.text


def test_flow_hooks_batch_and_completion(tmp_path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "fh.jsonl"))
    set_control_plane(plane)
    edges: list[tuple[str, str, str | None]] = []

    def log_flow(ctx: TransitionContext) -> None:
        if ctx.kind == "flow":
            edges.append((ctx.from_state.value, ctx.to_state.value, ctx.transition_kind))

    @flow(transition_hooks=[on_transition(log_flow)])
    def sample() -> int:
        return 42

    assert sample() == 42
    assert ("SCHEDULED", "PENDING", "propose") in edges
    assert ("PENDING", "RUNNING", "start") in edges
    assert ("RUNNING", "COMPLETED", "complete") in edges


def test_flow_hook_to_failed_wildcard(tmp_path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "ff.jsonl"))
    set_control_plane(plane)
    failures: list[TransitionContext] = []

    @flow(transition_hooks=[on_transition(lambda c: failures.append(c), to_state=RunState.FAILED)])
    def boom() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        boom()
    assert len(failures) == 1
    assert failures[0].from_state == RunState.RUNNING
    assert failures[0].to_state == RunState.FAILED


def test_task_hook_pending_to_running(tmp_path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "th.jsonl"))
    set_control_plane(plane)
    hits: list[TransitionContext] = []

    @task(transition_hooks=[on_transition(lambda c: hits.append(c), from_state=RunState.PENDING, to_state=RunState.RUNNING)])
    def inc(x: int) -> int:
        return x + 1

    @flow
    def sample() -> int:
        return inc.submit(1).result()

    assert sample() == 2
    assert len(hits) == 1
    assert hits[0].kind == "task"
    assert hits[0].event_type == "task_running"


def test_task_completed_metadata(tmp_path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "tc.jsonl"))
    set_control_plane(plane)
    captured: list[TransitionContext] = []

    @task(
        name="inc2",
        transition_hooks=[on_transition(lambda c: captured.append(c), to_state=RunState.COMPLETED)],
    )
    def inc2(x: int) -> int:
        return x + 1

    @flow
    def sample() -> int:
        return inc2.submit(5).result()

    assert sample() == 6
    assert len(captured) == 1
    assert captured[0].metadata is not None
    assert captured[0].metadata.get("task_name") == "inc2"


def test_flow_without_hooks_has_no_transition_callback(tmp_path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "plain.jsonl"))
    set_control_plane(plane)

    @flow
    def sample() -> int:
        return 1

    assert sample() == 1


@pytest.mark.parametrize("use_rust", ("1", "0"))
def test_hooks_with_rust_fsm_toggle(tmp_path, monkeypatch: pytest.MonkeyPatch, use_rust: str) -> None:
    monkeypatch.setenv("IRONFLOW_USE_RUST_FSM", use_rust)
    plane = InMemoryControlPlane(history_path=str(tmp_path / f"rust_{use_rust}.jsonl"))
    set_control_plane(plane)
    edges: list[str] = []

    @flow(transition_hooks=[on_transition(lambda c: edges.append(c.to_state.value) if c.kind == "flow" else None)])
    def sample() -> int:
        return 7

    assert sample() == 7
    assert "PENDING" in edges
    assert "RUNNING" in edges
    assert "COMPLETED" in edges
