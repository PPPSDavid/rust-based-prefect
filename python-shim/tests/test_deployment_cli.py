from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from fastapi.testclient import TestClient
from prefect_compat.cli.deployments import DeploymentCliClient
from prefect_compat.cli.main import main
from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app, control_plane


def _swap_plane(tmp_path: Path) -> None:
    history = tmp_path / "deployment-cli-history.jsonl"
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


def _patch_deployment_client(monkeypatch, http_client: TestClient) -> None:
    monkeypatch.setattr(
        "prefect_compat.cli.deployments.DeploymentCliClient",
        lambda base_url, session=None: DeploymentCliClient(
            base_url, session=http_client
        ),
    )


def _run(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def test_deployment_ls_inspect_run(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    http = TestClient(app)
    _patch_deployment_client(monkeypatch, http)

    created = http.post(
        "/api/deployments",
        json={"name": "cli-dep", "flow_name": "cli-dep-flow"},
    )
    assert created.status_code == 200, created.text
    dep = created.json()
    dep_id = dep["id"]

    code, out, err = _run(["deployment", "ls", "--api-url", "http://testserver"])
    assert code == 0, err
    assert "cli-dep" in out

    code, out, err = _run(
        ["deployment", "inspect", "cli-dep", "--api-url", "http://testserver"]
    )
    assert code == 0, err
    assert json.loads(out)["name"] == "cli-dep"

    code, out, err = _run(
        ["deployment", "inspect", dep_id, "--api-url", "http://testserver"]
    )
    assert code == 0, err
    assert json.loads(out)["id"] == dep_id

    code, out, err = _run(
        [
            "deployment",
            "run",
            "cli-dep",
            "--param",
            "n=1",
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 0, err
    payload = json.loads(out)
    assert "id" in payload or "deployment_run_id" in payload or "flow_run_id" in payload


def test_deployment_run_parameters_json(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    http = TestClient(app)
    _patch_deployment_client(monkeypatch, http)
    created = http.post(
        "/api/deployments",
        json={"name": "cli-dep-json", "flow_name": "cli-dep-json-flow"},
    )
    assert created.status_code == 200, created.text

    code, out, err = _run(
        [
            "deployment",
            "run",
            "cli-dep-json",
            "--parameters",
            '{"x": 2}',
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 0, err
    assert isinstance(json.loads(out), dict)


def test_deployment_inspect_missing(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_deployment_client(monkeypatch, TestClient(app))
    code, out, err = _run(
        [
            "deployment",
            "inspect",
            "does-not-exist",
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 1
    assert out == ""
    assert "Error:" in err
