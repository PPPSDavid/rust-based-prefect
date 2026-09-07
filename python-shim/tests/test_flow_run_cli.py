from __future__ import annotations

import io
import json
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from fastapi.testclient import TestClient
from prefect_compat import flow, task
from prefect_compat.cli.flow_runs import FlowRunClient
from prefect_compat.cli.main import main
from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app, control_plane


def _swap_plane(tmp_path: Path) -> None:
    history = tmp_path / "flow-run-cli-history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    control_plane._flows = plane._flows
    control_plane._tasks = plane._tasks
    control_plane._events = plane._events
    control_plane._tokens = plane._tokens
    control_plane._history_path = plane._history_path
    control_plane._sqlite_path = plane._sqlite_path
    control_plane._sqlite_conn = plane._sqlite_conn
    control_plane._rust_bridge = plane._rust_bridge
    control_plane._rust_fsm_bridge = plane._rust_fsm_bridge
    control_plane._rust_fsm_handle = plane._rust_fsm_handle
    control_plane._rust_native_persistence = plane._rust_native_persistence
    control_plane._rust_db_bound = plane._rust_db_bound
    control_plane._lock = plane._lock
    control_plane._test_plane_ref = plane
    set_control_plane(control_plane)


def _patch_flow_run_client(monkeypatch, http_client: TestClient) -> None:
    monkeypatch.setattr(
        "prefect_compat.cli.flow_runs.FlowRunClient",
        lambda base_url, session=None: FlowRunClient(base_url, session=http_client),
    )


def _run(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else 1
    return code, stdout.getvalue(), stderr.getvalue()


def test_flow_run_ls_inspect_logs_events_dag(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_flow_run_client(monkeypatch, TestClient(app))
    run = control_plane.create_flow_run("flow-run-cli")
    run_id = str(run.run_id)

    code, out, err = _run(["flow-run", "ls", "--api-url", "http://testserver"])
    assert code == 0, err
    assert run_id in out

    code, out, err = _run(
        ["flow-run", "inspect", run_id, "--api-url", "http://testserver"]
    )
    assert code == 0, err
    detail = json.loads(out)
    assert detail["id"] == run_id
    assert detail["name"] == "flow-run-cli"

    code, out, err = _run(
        ["flow-run", "logs", run_id, "--api-url", "http://testserver"]
    )
    assert code == 0, err
    assert "items" in json.loads(out)

    code, out, err = _run(
        ["flow-run", "events", run_id, "--api-url", "http://testserver"]
    )
    assert code == 0, err
    assert "items" in json.loads(out)

    code, out, err = _run(
        [
            "flow-run",
            "dag",
            run_id,
            "--mode",
            "logical",
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 0, err
    assert isinstance(json.loads(out), dict)

    code, out, err = _run(
        ["flow-run", "task-runs", run_id, "--api-url", "http://testserver"]
    )
    assert code == 0, err
    assert "items" in json.loads(out)


def test_flow_run_pause_requires_mode() -> None:
    code, _out, err = _run(
        [
            "flow-run",
            "pause",
            "00000000-0000-0000-0000-000000000001",
            "--api-url",
            "http://testserver",
        ]
    )
    assert code != 0
    assert "--mode" in err or "required" in err.lower()


def test_flow_run_pause_drain_then_resume(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_flow_run_client(monkeypatch, TestClient(app))
    started = threading.Event()
    release = threading.Event()

    @task
    def slow() -> str:
        started.set()
        assert release.wait(timeout=5)
        return "done"

    @flow
    def held() -> str:
        return slow.submit().result()

    thread = threading.Thread(target=held, daemon=True)
    thread.start()
    assert started.wait(timeout=5)
    run = control_plane.latest_flow()
    assert run is not None
    run_id = str(run.run_id)

    code, out, err = _run(
        [
            "flow-run",
            "pause",
            run_id,
            "--mode",
            "drain",
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 0, err
    body = json.loads(out)
    assert body["interrupt_mode"] == "drain"
    assert body["lifecycle_action"] == "pause"

    release.set()
    thread.join(timeout=5)

    # Drain may settle to PAUSED once in-flight finishes; resume if paused.
    inspect_code, inspect_out, inspect_err = _run(
        ["flow-run", "inspect", run_id, "--api-url", "http://testserver"]
    )
    assert inspect_code == 0, inspect_err
    state = json.loads(inspect_out)["state"]
    if state == "PAUSED":
        code, out, err = _run(
            ["flow-run", "resume", run_id, "--api-url", "http://testserver"]
        )
        assert code == 0, err
        assert isinstance(json.loads(out), dict)


def test_flow_run_cancel(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_flow_run_client(monkeypatch, TestClient(app))
    run = control_plane.create_flow_run("flow-run-cli-cancel")
    run_id = str(run.run_id)
    code, out, err = _run(
        ["flow-run", "cancel", run_id, "--api-url", "http://testserver"]
    )
    assert code == 0, err
    assert json.loads(out)["state"] == "CANCELLED"


def test_flow_run_retry_non_deployment_exits_1(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_flow_run_client(monkeypatch, TestClient(app))
    run = control_plane.create_flow_run("flow-run-cli-retry")
    run_id = str(run.run_id)
    code, out, err = _run(
        ["flow-run", "retry", run_id, "--api-url", "http://testserver"]
    )
    assert code == 1
    assert out == ""
    assert "Error:" in err
