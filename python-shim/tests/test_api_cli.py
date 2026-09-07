from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from fastapi.testclient import TestClient
from prefect_compat.cli.api import ApiClient
from prefect_compat.cli.main import main
from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app, control_plane


def _swap_plane(tmp_path: Path) -> None:
    history = tmp_path / "api-cli-history.jsonl"
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


def _patch_api_client(monkeypatch, http_client: TestClient) -> None:
    monkeypatch.setattr(
        "prefect_compat.cli.api.ApiClient",
        lambda base_url, session=None: ApiClient(base_url, session=http_client),
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


def test_api_get_list(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_api_client(monkeypatch, TestClient(app))
    control_plane.create_flow_run("api-cli-flow")
    code, out, err = _run(
        ["api", "GET", "/api/flow-runs", "--api-url", "http://testserver"]
    )
    assert code == 0, err
    payload = json.loads(out)
    assert "items" in payload
    assert any(item.get("name") == "api-cli-flow" for item in payload["items"])


def test_api_post_with_data(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_api_client(monkeypatch, TestClient(app))
    code, out, err = _run(
        [
            "api",
            "POST",
            "/api/work-pools",
            "--data",
            '{"name": "api-cli-pool", "type": "process"}',
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 0, err
    payload = json.loads(out)
    assert payload["name"] == "api-cli-pool"


def test_api_404_exits_1(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_api_client(monkeypatch, TestClient(app))
    code, out, err = _run(
        [
            "api",
            "GET",
            "/api/flow-runs/00000000-0000-0000-0000-000000000000",
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 1
    assert out == ""
    assert "not found" in err.lower() or "Flow run" in err or "detail" in err


def test_api_data_from_file(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_api_client(monkeypatch, TestClient(app))
    body_path = tmp_path / "body.json"
    body_path.write_text(
        json.dumps({"name": "api-cli-pool-file", "type": "process"}),
        encoding="utf-8",
    )
    code, out, err = _run(
        [
            "api",
            "POST",
            "/api/work-pools",
            "--data",
            f"@{body_path}",
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 0, err
    assert json.loads(out)["name"] == "api-cli-pool-file"


def test_api_invalid_query_exits_1() -> None:
    code, _out, err = _run(
        [
            "api",
            "GET",
            "/api/flow-runs",
            "--query",
            "bad",
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 1
    assert "invalid --query" in err


def test_api_query_params(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_api_client(monkeypatch, TestClient(app))
    control_plane.create_flow_run("api-cli-flow")
    code, out, err = _run(
        [
            "api",
            "GET",
            "/api/flow-runs",
            "--query",
            "limit=5",
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 0, err
    assert "items" in json.loads(out)


def test_api_rejects_array_body() -> None:
    code, _out, err = _run(
        [
            "api",
            "POST",
            "/api/work-pools",
            "--data",
            "[]",
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 1
    assert "JSON object" in err


def test_api_invalid_json_data() -> None:
    code, _out, err = _run(
        [
            "api",
            "POST",
            "/api/work-pools",
            "--data",
            "{not-json",
            "--api-url",
            "http://testserver",
        ]
    )
    assert code == 1
    assert "Error:" in err
