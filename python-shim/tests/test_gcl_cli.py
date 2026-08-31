from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from fastapi.testclient import TestClient
from prefect_compat.cli.gcl import GclClient
from prefect_compat.cli.main import main
from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app, control_plane


def _swap_plane(tmp_path: Path) -> None:
    history = tmp_path / "gcl-history.jsonl"
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


def _patch_gcl_client(monkeypatch, http_client: TestClient) -> None:
    monkeypatch.setattr(
        "prefect_compat.cli.gcl.GclClient",
        lambda base_url, session=None: GclClient(base_url, session=http_client),
    )


def _run(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def test_gcl_ls_empty(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_gcl_client(monkeypatch, TestClient(app))
    code, out, err = _run(["gcl", "ls", "--api-url", "http://testserver"])
    assert code == 0, err
    assert json.loads(out) == []


def test_gcl_create_ls_inspect_update_delete(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_gcl_client(monkeypatch, TestClient(app))
    argv_url = ["--api-url", "http://testserver"]

    code, out, err = _run(
        ["gcl", "create", "db", "--limit", "5", "--decay", "1.5", *argv_url]
    )
    assert code == 0, err
    created = json.loads(out)
    assert created["name"] == "db"
    assert created["limit"] == 5
    assert created["slot_decay_per_second"] == 1.5
    assert created["active"] is True

    code, out, err = _run(["gcl", "ls", *argv_url])
    assert code == 0, err
    listed = json.loads(out)
    assert any(item["name"] == "db" for item in listed)

    code, out, err = _run(["gcl", "inspect", "db", *argv_url])
    assert code == 0, err
    inspected = json.loads(out)
    assert inspected["name"] == "db"
    assert inspected["limit"] == 5
    assert "active_slots" in inspected

    code, out, err = _run(
        ["gcl", "update", "db", "--limit", "8", "--inactive", *argv_url]
    )
    assert code == 0, err
    updated = json.loads(out)
    assert updated["limit"] == 8
    assert updated["active"] is False

    code, out, err = _run(["gcl", "update", "db", "--active", *argv_url])
    assert code == 0, err
    assert json.loads(out)["active"] is True

    code, out, err = _run(["gcl", "delete", "db", *argv_url])
    assert code == 0, err
    deleted = json.loads(out)
    assert deleted.get("deleted") is True or deleted.get("ok") is True

    code, out, err = _run(["gcl", "inspect", "db", *argv_url])
    assert code == 1
    assert "not found" in err


def test_gcl_tag_name_roundtrip(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_gcl_client(monkeypatch, TestClient(app))
    argv_url = ["--api-url", "http://testserver"]

    code, out, err = _run(["gcl", "create", "tag:db", "--limit", "2", *argv_url])
    assert code == 0, err
    assert json.loads(out)["name"] == "tag:db"

    code, out, err = _run(["gcl", "inspect", "tag:db", *argv_url])
    assert code == 0, err
    assert json.loads(out)["limit"] == 2

    code, out, err = _run(["gcl", "delete", "tag:db", *argv_url])
    assert code == 0, err


def test_gcl_inspect_missing_exits_1(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_gcl_client(monkeypatch, TestClient(app))
    code, _out, err = _run(
        ["gcl", "inspect", "missing", "--api-url", "http://testserver"]
    )
    assert code == 1
    assert "not found" in err


def test_gcl_update_requires_fields(tmp_path: Path, monkeypatch) -> None:
    _swap_plane(tmp_path)
    _patch_gcl_client(monkeypatch, TestClient(app))
    code, _out, err = _run(["gcl", "update", "db", "--api-url", "http://testserver"])
    assert code == 1
    assert "--limit" in err
