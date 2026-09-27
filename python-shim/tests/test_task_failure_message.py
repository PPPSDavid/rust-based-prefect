"""Failed tasks expose the exception on the task row and the log line."""

from __future__ import annotations

from pathlib import Path

import pytest
from prefect_compat import flow, set_control_plane, task
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.task_failure_message import (
    exception_failure_details,
    task_event_log_message,
)


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "history.jsonl"))
    set_control_plane(plane)
    return plane


def test_exception_without_traceback_omits_stack() -> None:
    details = exception_failure_details(RuntimeError("no stack"))
    assert details == {"error": "no stack"}
    assert "traceback" not in details


def test_log_line_appends_error_only_for_task_failed() -> None:
    assert task_event_log_message("inc", "task_completed", {"error": "nope"}) == (
        "inc: task_completed"
    )
    assert (
        task_event_log_message("explode", "task_failed", None) == "explode: task_failed"
    )
    assert (
        task_event_log_message(
            "explode",
            "task_failed",
            {"error": "intentional failure", "traceback": "Traceback: boom"},
        )
        == "explode: task_failed: intentional failure\nTraceback: boom"
    )


def test_failed_task_exposes_error_traceback_and_log(tmp_path: Path) -> None:
    plane = _plane(tmp_path)

    @task
    def explode() -> None:
        raise RuntimeError("intentional failure for DAG/state testing")

    @task
    def inc() -> int:
        return 1

    @flow()
    def failing_flow() -> None:
        inc.submit().result()
        explode.submit().result()

    with pytest.raises(RuntimeError, match="intentional failure"):
        failing_flow()

    flow_run = plane.latest_flow()
    assert flow_run is not None
    items = plane.list_task_runs(flow_run.run_id).items
    by_name = {item["task_name"]: item for item in items}
    failed = by_name["explode"]
    assert failed["state"] == "FAILED"
    assert failed["error"] == "intentional failure for DAG/state testing"
    assert "Traceback (most recent call last):" in failed["traceback"]
    assert "intentional failure for DAG/state testing" in failed["traceback"]
    assert "error" not in by_name["inc"]

    logs = plane.list_logs(flow_run.run_id, limit=50).items
    failed_logs = [
        row
        for row in logs
        if row["task_run_id"] == failed["id"] and row["level"] == "ERROR"
    ]
    assert failed_logs
    message = failed_logs[0]["message"]
    assert message.startswith(
        "explode: task_failed: intentional failure for DAG/state testing"
    )
    assert "Traceback (most recent call last):" in message
