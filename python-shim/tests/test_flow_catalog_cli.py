from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from fastapi.testclient import TestClient
from prefect_compat.cli.flows import FlowCatalogClient
from prefect_compat.cli.main import main
from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app, control_plane


def _swap_plane(tmp_path: Path) -> None:
    history = tmp_path / "flow-cli-history.jsonl"
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


def _run(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def test_flow_ls_and_inspect(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    http = TestClient(app)
    monkeypatch.setattr(
        "prefect_compat.cli.flows.FlowCatalogClient",
        lambda base_url, session=None: FlowCatalogClient(base_url, session=http),
    )
    control_plane.create_flow_run("cli-flow")
    code, out, err = _run(["flow", "ls", "--api-url", "http://testserver"])
    assert code == 0, err
    assert "cli-flow" in out
    code, out, err = _run(
        ["flow", "inspect", "cli-flow", "--api-url", "http://testserver"]
    )
    assert code == 0, err
    assert "cli-flow" in out


def test_deploy_prune_requires_all() -> None:
    code, _out, err = _run(["deploy", "--prune", "--name", "x"])
    assert code == 1
    assert "--prune requires --all" in err
