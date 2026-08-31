"""Return-value rewrite: a non-None terminal RunState overrides destination."""

from __future__ import annotations

import logging
import threading
from uuid import uuid4

import pytest
from prefect_compat import (
    FlowChildrenFailed,
    InMemoryControlPlane,
    RunState,
    TransitionContext,
    TransitionRewriteFailed,
    flow,
    on_transition,
    set_control_plane,
    task,
)
from prefect_compat.cancellation import FlowRunCancelled
from prefect_compat.hooks import (
    compile_transition_hooks,
    resolve_terminal_rewrite,
)


def _plane(tmp_path, name: str = "rw") -> InMemoryControlPlane:
    plane = InMemoryControlPlane(history_path=str(tmp_path / f"{name}.jsonl"))
    set_control_plane(plane)
    return plane


def test_first_non_none_rewrite_wins() -> None:
    calls: list[str] = []

    def first(ctx: TransitionContext) -> RunState:
        calls.append("first")
        return RunState.COMPLETED

    def second(ctx: TransitionContext) -> RunState:
        calls.append("second")
        return RunState.CANCELLED

    specs = compile_transition_hooks(
        (
            on_transition(
                first,
                from_state=RunState.RUNNING,
                to_state=RunState.FAILED,
            ),
            on_transition(
                second,
                from_state=RunState.RUNNING,
                to_state=RunState.FAILED,
            ),
        )
    )
    probe = resolve_terminal_rewrite(
        specs,
        TransitionContext(
            kind="task",
            flow_run_id=uuid4(),
            from_state=RunState.RUNNING,
            to_state=RunState.FAILED,
        ),
    )
    assert probe.to_state == RunState.COMPLETED
    assert calls == ["first"]


def test_illegal_rewrite_return_keeps_proposed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def bad_state(ctx: TransitionContext) -> RunState:
        return RunState.PAUSED

    def not_decision(ctx: TransitionContext) -> str:
        return "nope"

    specs = compile_transition_hooks(
        (
            on_transition(bad_state, to_state=RunState.FAILED),
            on_transition(not_decision, to_state=RunState.FAILED),
        )
    )
    with caplog.at_level(logging.WARNING, logger="prefect_compat.hooks"):
        probe = resolve_terminal_rewrite(
            specs,
            TransitionContext(
                kind="flow",
                flow_run_id=uuid4(),
                from_state=RunState.RUNNING,
                to_state=RunState.FAILED,
            ),
        )
    assert probe.to_state is None
    assert "illegal to_state" in caplog.text
    assert "expected RunState" in caplog.text


def test_rewrite_exception_keeps_proposed(caplog: pytest.LogCaptureFixture) -> None:
    def boom(ctx: TransitionContext) -> RunState:
        raise RuntimeError("rewrite oops")

    specs = compile_transition_hooks((on_transition(boom, to_state=RunState.FAILED),))
    with caplog.at_level(logging.ERROR, logger="prefect_compat.hooks"):
        probe = resolve_terminal_rewrite(
            specs,
            TransitionContext(
                kind="flow",
                flow_run_id=uuid4(),
                from_state=RunState.RUNNING,
                to_state=RunState.FAILED,
            ),
        )
    assert probe.to_state is None
    assert "transition rewrite handler failed" in caplog.text


def test_returning_runstate_rewrites() -> None:
    specs = compile_transition_hooks(
        (on_transition(lambda _c: RunState.COMPLETED, to_state=RunState.FAILED),)
    )
    probe = resolve_terminal_rewrite(
        specs,
        TransitionContext(
            kind="flow",
            flow_run_id=uuid4(),
            from_state=RunState.RUNNING,
            to_state=RunState.FAILED,
        ),
    )
    assert probe.to_state == RunState.COMPLETED


def test_observe_sees_committed_edge_after_task_salvage(tmp_path) -> None:
    plane = _plane(tmp_path, "observe")
    observed: list[str] = []

    def salvage(ctx: TransitionContext) -> RunState:
        return RunState.COMPLETED

    def on_failed(ctx: TransitionContext) -> None:
        observed.append("failed")

    def on_completed(ctx: TransitionContext) -> None:
        observed.append("completed")

    @task(
        transition_hooks=[
            on_transition(
                salvage,
                from_state=RunState.RUNNING,
                to_state=RunState.FAILED,
            ),
            on_transition(on_failed, to_state=RunState.FAILED),
            on_transition(on_completed, to_state=RunState.COMPLETED),
        ]
    )
    def flaky() -> int:
        raise RuntimeError("transient")

    @flow
    def pipeline() -> int:
        return flaky.submit().result()

    assert pipeline() is None
    assert observed == ["completed"]
    flow_run = plane.latest_flow()
    assert flow_run is not None
    assert flow_run.state == RunState.COMPLETED
    tasks = [t for t in plane._tasks.values() if t.flow_run_id == flow_run.run_id]
    assert len(tasks) == 1
    assert tasks[0].state == RunState.COMPLETED
    events = plane.list_events(flow_run.run_id, limit=200).items
    failed_events = [e for e in events if e.get("event_type") == "task_failed"]
    completed_events = [e for e in events if e.get("event_type") == "task_completed"]
    assert failed_events == []
    assert completed_events
    data = completed_events[-1].get("data") or {}
    assert data.get("rewritten_from") == "FAILED"
    assert data.get("proposed_to_state") == "FAILED"


def test_task_demote_fails_wait_all_parent(tmp_path) -> None:
    plane = _plane(tmp_path, "demote")

    def demote(ctx: TransitionContext) -> RunState:
        return RunState.FAILED

    @task(
        transition_hooks=[
            on_transition(
                demote,
                from_state=RunState.RUNNING,
                to_state=RunState.COMPLETED,
            )
        ]
    )
    def ok() -> int:
        return 1

    @flow
    def pipeline() -> int:
        try:
            ok.submit().result()
        except TransitionRewriteFailed:
            return 0
        return 0

    with pytest.raises(FlowChildrenFailed):
        pipeline()
    flow_run = plane.latest_flow()
    assert flow_run is not None
    assert flow_run.state == RunState.FAILED
    tasks = [t for t in plane._tasks.values() if t.flow_run_id == flow_run.run_id]
    assert tasks[0].state == RunState.FAILED


def test_flow_body_exception_salvage(tmp_path) -> None:
    plane = _plane(tmp_path, "flow-salvage")

    def salvage(ctx: TransitionContext) -> RunState:
        return RunState.COMPLETED

    @flow(
        final_state="explicit",
        transition_hooks=[
            on_transition(
                salvage,
                from_state=RunState.RUNNING,
                to_state=RunState.FAILED,
            )
        ],
    )
    def boom() -> str:
        raise RuntimeError("nope")

    assert boom() is None
    flow_run = plane.latest_flow()
    assert flow_run is not None
    assert flow_run.state == RunState.COMPLETED


def test_wait_all_child_fail_salvage(tmp_path) -> None:
    plane = _plane(tmp_path, "wait-all-salvage")

    def salvage_flow(ctx: TransitionContext) -> RunState:
        return RunState.COMPLETED

    @task
    def flaky() -> int:
        raise RuntimeError("child boom")

    @flow(
        transition_hooks=[
            on_transition(
                salvage_flow,
                from_state=RunState.RUNNING,
                to_state=RunState.FAILED,
            )
        ]
    )
    def pipeline() -> str:
        try:
            flaky.submit().result()
        except RuntimeError:
            return "body-ok"
        return "unreachable"

    assert pipeline() == "body-ok"
    flow_run = plane.latest_flow()
    assert flow_run is not None
    assert flow_run.state == RunState.COMPLETED


def test_wait_all_still_fails_without_rewrite(tmp_path) -> None:
    _plane(tmp_path, "wait-all-fail")

    @task
    def flaky() -> int:
        raise RuntimeError("child boom")

    @flow
    def pipeline() -> int:
        return flaky.submit().result()

    with pytest.raises((RuntimeError, FlowChildrenFailed)):
        pipeline()


def test_operator_cancel_does_not_consult_rewrite(tmp_path) -> None:
    plane = _plane(tmp_path, "cancel")
    rewrite_calls: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def salvage_cancel(ctx: TransitionContext) -> RunState:
        rewrite_calls.append("cancel")
        return RunState.COMPLETED

    @task(
        transition_hooks=[
            on_transition(
                salvage_cancel,
                from_state=RunState.RUNNING,
                to_state=RunState.CANCELLED,
            )
        ]
    )
    def slow() -> str:
        started.set()
        release.wait(timeout=5)
        return "x"

    @flow(
        transition_hooks=[
            on_transition(
                salvage_cancel,
                from_state=RunState.RUNNING,
                to_state=RunState.CANCELLED,
            )
        ]
    )
    def f() -> str:
        return slow.submit().result()

    def _run() -> None:
        try:
            f()
        except FlowRunCancelled:
            return

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert started.wait(timeout=5)
    run = plane.latest_flow()
    assert run is not None
    detail = plane.cancel_flow_run(run.run_id)
    assert detail["state"] == "CANCELLED"
    release.set()
    thread.join(timeout=5)
    assert rewrite_calls == []
    current = plane.get_flow(run.run_id)
    assert current.state == RunState.CANCELLED


def test_cache_hit_does_not_run_rewrite(tmp_path) -> None:
    plane = _plane(tmp_path, "cache")
    rewrite_calls: list[int] = []

    def count_rewrite(ctx: TransitionContext) -> None:
        rewrite_calls.append(1)
        return None

    @task(
        persist_result=True,
        transition_hooks=[
            on_transition(
                count_rewrite,
                from_state=RunState.RUNNING,
                to_state=RunState.COMPLETED,
            )
        ],
    )
    def boxed() -> int:
        return 3

    @flow
    def pipeline() -> int:
        return boxed.submit().result()

    assert pipeline() == 3
    first = plane.latest_flow()
    assert first is not None
    assert rewrite_calls == [1]
    plane.prepare_resume(first.run_id)
    rewrite_calls.clear()
    assert pipeline() == 3
    assert rewrite_calls == []
