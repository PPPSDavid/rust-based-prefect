from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from prefect_compat.decorators import flow, set_control_plane
from prefect_compat.errors import FlowCatalogConflict
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app, control_plane


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    history = tmp_path / "catalog-history.jsonl"
    return InMemoryControlPlane(history_path=str(history))


def _swap_plane(tmp_path: Path) -> InMemoryControlPlane:
    plane = _plane(tmp_path)
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
    control_plane._lock = plane._lock
    control_plane._test_plane_ref = plane
    set_control_plane(control_plane)
    return plane


def _flow_id(plane: InMemoryControlPlane, name: str) -> str:
    row = plane._get_flow_catalog_by_name(name)
    assert row is not None
    return str(row["id"])


def test_create_flow_run_attaches_catalog_id(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    run = plane.create_flow_run("alpha")
    rows = plane._query_rows(
        "SELECT flow_id FROM flow_runs WHERE id = ?", [str(run.run_id)]
    )
    assert rows and rows[0]["flow_id"]
    catalog = plane._get_flow_catalog(str(rows[0]["flow_id"]))
    assert catalog is not None
    assert catalog["name"] == "alpha"


def test_rename_keeps_uuid_and_reserves_alias(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    plane.create_flow_run("A")
    flow_id = _flow_id(plane, "A")
    renamed = plane.rename_flow(flow_id, "B")
    assert renamed["id"] == flow_id
    assert renamed["name"] == "B"
    assert "A" in renamed["aliases"]
    detail = plane.get_flow_catalog_detail("A")
    assert detail is not None
    assert detail["name"] == "B"
    assert detail["resolved_from_alias"] is True
    with pytest.raises(FlowCatalogConflict) as exc:
        plane.ensure_flow("A")
    assert exc.value.code == "alias_reserved"


def test_rename_blocked_by_undeleted_deployment(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    plane.create_deployment(name="A-prod", flow_name="A")
    flow_id = _flow_id(plane, "A")
    with pytest.raises(FlowCatalogConflict) as exc:
        plane.rename_flow(flow_id, "B")
    assert exc.value.code == "undeleted_deployments"


def test_paused_deployment_still_blocks_archive(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    dep = plane.create_deployment(name="A-prod", flow_name="A", paused=True)
    assert dep["paused"] is True
    flow_id = _flow_id(plane, "A")
    with pytest.raises(FlowCatalogConflict) as exc:
        plane.archive_flow(flow_id)
    assert exc.value.code == "undeleted_deployments"


def test_archive_hides_from_default_list(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    plane.create_flow_run("solo")
    flow_id = _flow_id(plane, "solo")
    plane.archive_flow(flow_id)
    active = plane.list_flows(status="active")
    assert all(item["name"] != "solo" for item in active.items)
    archived = plane.list_flows(status="archived")
    assert any(item["name"] == "solo" for item in archived.items)


def test_delete_deployment_suffixes_name(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    dep = plane.create_deployment(name="A-prod", flow_name="A")
    deleted = plane.delete_deployment(UUID(dep["id"]))
    assert deleted["deleted"] is True
    assert plane.get_deployment_by_name("A-prod") is None
    rows = plane._query_rows(
        "SELECT name, deleted_at FROM deployments WHERE id = ?", [dep["id"]]
    )
    assert rows[0]["deleted_at"]
    assert str(rows[0]["name"]).startswith("A-prod__deleted__")


def test_delete_deployment_blocked_when_schedule_enabled(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    dep = plane.create_deployment(
        name="sched",
        flow_name="A",
        schedule_interval_seconds=60,
        schedule_enabled=True,
        schedule_next_run_at=datetime.now(UTC).isoformat(),
    )
    with pytest.raises(FlowCatalogConflict) as exc:
        plane.delete_deployment(UUID(dep["id"]))
    assert exc.value.code == "schedule_enabled"


def test_apply_prune_renames_and_archives(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    plane.create_deployment(name="A-prod", flow_name="A")
    flow_id = _flow_id(plane, "A")
    result = plane.apply_deployments(
        [
            {
                "name": "B-prod",
                "flow_name": "B",
                "formerly": ["A"],
            }
        ],
        prune=True,
    )
    assert any(
        item["id"] == flow_id and item["name"] == "B" for item in result["renamed"]
    )
    assert plane.get_deployment_by_name("A-prod") is None
    assert plane.get_deployment_by_name("B-prod") is not None
    catalog = plane._get_flow_catalog(flow_id)
    assert catalog is not None
    assert catalog["name"] == "B"


def test_prune_last_deployment_auto_archives(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    plane.create_deployment(name="B-prod", flow_name="B")
    result = plane.apply_deployments([], prune=True)
    assert any(item["name"] == "B" for item in result["archived"])
    archived = plane.list_flows(status="archived")
    assert any(item["name"] == "B" for item in archived.items)


def test_retention_skips_live_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IRONFLOW_RUN_RETENTION_DAYS", "1")
    plane = _plane(tmp_path)
    done = plane.create_flow_run("ttl")
    live = plane.create_flow_run("ttl")
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    plane._sqlite_conn.execute(
        "UPDATE flow_runs SET state = 'COMPLETED', updated_at = ? WHERE id = ?",
        [old, str(done.run_id)],
    )
    plane._sqlite_conn.execute(
        "UPDATE flow_runs SET state = 'RUNNING', updated_at = ? WHERE id = ?",
        [old, str(live.run_id)],
    )
    summary = plane.retention_sweep()
    assert summary["deleted_runs"] >= 1
    remaining = plane._query_rows("SELECT id, state FROM flow_runs", [])
    ids = {row["id"] for row in remaining}
    assert str(live.run_id) in ids
    assert str(done.run_id) not in ids


def test_formerly_decorator_renames_when_no_deployments(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    plane.create_flow_run("old_name")
    flow_id = _flow_id(plane, "old_name")

    @flow(name="new_name", formerly=["old_name"])
    def pipeline() -> str:
        return "ok"

    pipeline()
    catalog = plane._get_flow_catalog(flow_id)
    assert catalog is not None
    assert catalog["name"] == "new_name"


def test_http_catalog_lifecycle(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    client = TestClient(app)
    info = client.get("/api/server-info")
    assert info.status_code == 200
    assert "catalog_hide_archived" in info.json()
    control_plane.create_flow_run("http-flow")
    listed = client.get("/api/flows?status=active")
    assert listed.status_code == 200
    items = listed.json()["items"]
    match = next(item for item in items if item["name"] == "http-flow")
    flow_id = match["id"]
    renamed = client.post(f"/api/flows/{flow_id}/rename", json={"name": "http-flow-2"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "http-flow-2"
    alias = client.get("/api/flows/http-flow")
    assert alias.status_code == 200
    assert alias.json()["resolved_from_alias"] is True
    archived = client.post(f"/api/flows/{flow_id}/archive")
    assert archived.status_code == 200
    restored = client.post(f"/api/flows/{flow_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"


def test_http_rename_conflict_with_deployment(tmp_path: Path) -> None:
    _swap_plane(tmp_path)
    client = TestClient(app)
    control_plane.create_deployment(name="live-prod", flow_name="live")
    flow_id = _flow_id(control_plane, "live")
    resp = client.post(f"/api/flows/{flow_id}/rename", json={"name": "other"})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "undeleted_deployments"
