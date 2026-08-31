"""P3.0/P3.1: get_run_context + get_run_logger → control-plane log rows."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from prefect_compat import (
    flow,
    get_run_context,
    get_run_logger,
    set_control_plane,
    task,
)
from prefect_compat.context import MissingContextError
from prefect_compat.runtime import InMemoryControlPlane


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / "ctx-logs.jsonl"))


def test_get_run_context_outside_flow_raises() -> None:
    with pytest.raises(MissingContextError):
        get_run_context()


def test_get_run_context_in_flow_and_task(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    seen: dict[str, object] = {}

    @task
    def work(x: int) -> int:
        ctx = get_run_context()
        seen["task_flow_run_id"] = ctx.flow_run_id
        seen["task_run_id"] = ctx.task_run_id
        seen["task_name"] = ctx.task_name
        seen["flow_name"] = ctx.flow_name
        seen["parameters"] = dict(ctx.parameters or {})
        assert ctx.task_run_id is not None
        return x + 1

    @flow(name="ctx-flow")
    def f(n: int) -> int:
        ctx = get_run_context()
        seen["flow_run_id"] = ctx.flow_run_id
        seen["flow_only_task_run_id"] = ctx.task_run_id
        seen["flow_parameters"] = dict(ctx.parameters or {})
        assert ctx.task_run_id is None
        assert ctx.flow_name == "ctx-flow"
        return work.submit(n).result()

    assert f(3) == 4
    assert seen["flow_only_task_run_id"] is None
    assert seen["flow_run_id"] == seen["task_flow_run_id"]
    assert isinstance(seen["flow_run_id"], UUID)
    assert isinstance(seen["task_run_id"], UUID)
    assert seen["task_name"] == "work"
    assert seen["flow_name"] == "ctx-flow"
    assert seen["flow_parameters"] == {"n": 3}
    assert seen["parameters"] == {"n": 3}


def test_get_run_logger_writes_flow_and_task_logs(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @task
    def noisy() -> str:
        log = get_run_logger()
        log.info("hello from task")
        log.warning("warn from task")
        return "ok"

    @flow
    def f() -> str:
        log = get_run_logger("custom")
        log.info("hello from flow")
        return noisy.submit().result()

    assert f() == "ok"
    run = plane.latest_flow()
    assert run is not None
    page = plane.list_logs(run.run_id, limit=200)
    messages = {item["message"] for item in page.items}
    assert "hello from flow" in messages
    assert "hello from task" in messages
    assert "warn from task" in messages

    task_info = next(i for i in page.items if i["message"] == "hello from task")
    assert task_info["level"] == "INFO"
    assert task_info["task_run_id"] is not None
    assert task_info["flow_run_id"] == str(run.run_id)

    warn = next(i for i in page.items if i["message"] == "warn from task")
    assert warn["level"] == "WARNING"

    flow_info = next(i for i in page.items if i["message"] == "hello from flow")
    assert flow_info["task_run_id"] is None


def test_get_run_logger_outside_run_does_not_raise(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    log = get_run_logger()
    log.info("orphan message")
    # Outside a run: stderr fallback, no crash, no durable row for a fake id.
    err = capsys.readouterr().err
    assert "orphan message" in err


def test_logger_does_not_require_holding_flow_for_list(tmp_path: Path) -> None:
    """User logs remain queryable after the flow ContextVar is cleared."""
    plane = _plane(tmp_path)
    set_control_plane(plane)

    @flow
    def f() -> UUID:
        get_run_logger().info("persisted")
        return get_run_context().flow_run_id

    run_id = f()
    page = plane.list_logs(run_id, limit=50)
    assert any(i["message"] == "persisted" for i in page.items)


def test_concurrent_logger_writes_are_safe(tmp_path: Path) -> None:
    import threading

    plane = _plane(tmp_path)
    set_control_plane(plane)
    errors: list[BaseException] = []

    @flow
    def f() -> UUID:
        run_id = get_run_context().flow_run_id

        def _worker(i: int) -> None:
            try:
                for j in range(20):
                    # Direct plane writes stress the store lock (ContextVars are
                    # not inherited by raw threads without copy_context).
                    plane.append_log(
                        flow_run_id=run_id,
                        message=f"msg-{i}-{j}",
                        level="INFO",
                    )
            except BaseException as exc:  # noqa: BLE001 — capture for assertion
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        return run_id

    run_id = f()
    assert errors == []
    page = plane.list_logs(run_id, limit=2000)
    user_msgs = [i for i in page.items if str(i["message"]).startswith("msg-")]
    assert len(user_msgs) == 8 * 20
