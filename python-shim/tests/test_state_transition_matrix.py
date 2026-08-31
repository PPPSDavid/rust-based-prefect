"""Scenario tests aligned with docs/concepts/state-transition-matrix.md."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task
from prefect_compat.graph_mode import resolve_graph_mode
from prefect_compat.runtime import RunState


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    history = tmp_path / "history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    set_control_plane(plane)
    return plane


@task
def noop() -> None:
    return None


@flow()
def happy_flow() -> None:
    noop.submit().result()


def test_happy_path_completes(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    happy_flow()
    latest = plane.latest_flow()
    assert latest is not None
    rec = plane.get_flow(latest.run_id)
    assert rec.state == RunState.COMPLETED


def test_scheduled_to_cancelled_allowed(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    run = plane.create_flow_run("cancel-early")
    plane.set_flow_state(run.run_id, RunState.CANCELLED, uuid4(), "user_cancel")
    rec = plane.get_flow(run.run_id)
    assert rec.state == RunState.CANCELLED


def test_terminal_cannot_transition_to_running(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    happy_flow()
    latest = plane.latest_flow()
    assert latest is not None
    rec = plane.get_flow(latest.run_id)
    with pytest.raises(ValueError, match="invalid transition|Invalid"):
        plane.set_flow_state(rec.run_id, RunState.RUNNING, uuid4(), "bad")


def test_retry_creates_new_flow_run_preserves_lineage(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    run = plane.create_flow_run("lineage-test")
    plane.configure_flow_graph_mode(
        run.run_id,
        resolve_graph_mode(
            "auto",
            fallback_required=False,
            manifest={"nodes": [{"node_id": "n1", "task_name": "t", "deps": []}]},
        ),
    )
    second = plane.create_flow_run(
        "lineage-test",
        resume_from_flow_run_id=run.run_id,
        parameters_fingerprint="fp",
    )
    assert second.run_id != run.run_id
    assert second.flow_attempt_number == 2
    detail = plane.get_flow_run_detail(second.run_id)
    assert detail is not None
    assert detail["flow_attempt_number"] == 2


def test_task_run_attempt_exposed_on_create(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    flow_run = plane.create_flow_run("attempt-test")
    task = plane.create_task_run(flow_run.run_id, "work", planned_node_id="n1")
    assert task.task_run_attempt == 1
    listed = plane.list_task_runs(flow_run.run_id).items
    assert listed[0]["task_run_attempt"] == 1
