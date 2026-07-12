from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from fastapi.testclient import TestClient

from prefect_compat.cli.main import main
from prefect_compat.decorators import set_control_plane
from prefect_compat.deploy.client import DeployClient
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app, control_plane


def _swap_plane(tmp_path: Path) -> None:
    history = tmp_path / "deployments-history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    control_plane._flows = plane._flows
    control_plane._tasks = plane._tasks
    control_plane._events = plane._events
    control_plane._tokens = plane._tokens
    control_plane._history_path = plane._history_path
    control_plane._sqlite_path = plane._sqlite_path
    control_plane._sqlite_conn = plane._sqlite_conn
    control_plane._manifest_by_task = plane._manifest_by_task
    control_plane._rust_bridge = plane._rust_bridge
    control_plane._rust_fsm_bridge = plane._rust_fsm_bridge
    control_plane._rust_fsm_handle = plane._rust_fsm_handle
    control_plane._rust_native_persistence = plane._rust_native_persistence
    control_plane._rust_db_bound = plane._rust_db_bound
    control_plane._test_plane_ref = plane
    set_control_plane(control_plane)


def _write_manifest(path: Path, *, extra: str = "") -> None:
    path.write_text(
        f"""\
ironflow-version: "1"
deployments:
  - name: cli-deploy-a
    flow_name: simple_flow
    work_pool:
      name: default-process-pool
  - name: cli-deploy-b
    flow_name: wide_flow
    work_pool:
      name: default-process-pool
{extra}""",
        encoding="utf-8",
    )


def test_init_creates_yaml(tmp_path: Path) -> None:
    stdout = io.StringIO()
    code = main(["init", "--directory", str(tmp_path)])
    assert code == 0

    manifest_path = tmp_path / "ironflow.yaml"
    assert manifest_path.is_file()
    text = manifest_path.read_text(encoding="utf-8")
    assert "ironflow-version:" in text
    assert "deployments:" in text

    with redirect_stdout(stdout):
        code_again = main(["init", "--directory", str(tmp_path)])
    assert code_again == 0
    assert "exists:" in stdout.getvalue()


def test_deploy_dry_run_prints_action(tmp_path: Path, capsys, monkeypatch) -> None:
    _swap_plane(tmp_path)
    manifest_path = tmp_path / "ironflow.yaml"
    _write_manifest(manifest_path)

    http_client = TestClient(app)
    monkeypatch.setattr(
        "prefect_compat.cli.main.DeployClient",
        lambda base_url, session=None: DeployClient(base_url, session=http_client),
    )

    code = main(
        [
            "deploy",
            "--file",
            str(manifest_path),
            "--name",
            "cli-deploy-a",
            "--dry-run",
        ]
    )
    assert code == 0

    captured = capsys.readouterr()
    assert "dry-run: would create deployment 'cli-deploy-a'" in captured.out


def test_deploy_all_with_testclient(tmp_path: Path, capsys, monkeypatch) -> None:
    _swap_plane(tmp_path)
    manifest_path = tmp_path / "ironflow.yaml"
    _write_manifest(manifest_path)

    http_client = TestClient(app)
    monkeypatch.setattr(
        "prefect_compat.cli.main.DeployClient",
        lambda base_url, session=None: DeployClient(base_url, session=http_client),
    )
    deploy_client = DeployClient(session=http_client)

    code = main(
        [
            "deploy",
            "--file",
            str(manifest_path),
            "--all",
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 0

    captured = capsys.readouterr()
    assert "create: deployment 'cli-deploy-a'" in captured.out
    assert "create: deployment 'cli-deploy-b'" in captured.out

    assert deploy_client.find_deployment_by_name("cli-deploy-a") is not None
    assert deploy_client.find_deployment_by_name("cli-deploy-b") is not None


def test_deploy_missing_manifest_returns_error(tmp_path: Path) -> None:
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(["deploy", "--file", str(tmp_path / "missing.yaml"), "--all"])
    assert code == 1
    assert "manifest not found" in stderr.getvalue()
