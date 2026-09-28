"""Server-side flow-run list filters and pagination."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from prefect_compat.control_plane.flow_run_list import (
    prepare_flow_run_list_filters,
    query_flow_runs_page,
)
from prefect_compat.decorators import flow, set_control_plane
from prefect_compat.flow_catalog_settings import catalog_hide_archived
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "history.jsonl"))
    set_control_plane(plane)
    return plane


def test_search_paginates_on_the_server(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    plane.create_flow_run("needle-flow", tags=["batch"])
    plane.create_flow_run("noise-0")
    plane.create_flow_run("noise-1")

    first_page = plane.list_flow_runs(limit=1)
    assert first_page.items[0]["name"] == "noise-1"
    assert first_page.next_cursor

    found = plane.list_flow_runs(limit=1, q="needle")
    assert [item["name"] for item in found.items] == ["needle-flow"]
    assert found.items[0]["tags"] == ["batch"]
    assert found.next_cursor is None

    by_tag = plane.list_flow_runs(q="batch")
    assert [item["name"] for item in by_tag.items] == ["needle-flow"]

    page = plane.list_flow_runs(limit=1, q="noise")
    nxt = plane.list_flow_runs(limit=1, q="noise", cursor=page.next_cursor)
    names = {page.items[0]["name"], nxt.items[0]["name"]}
    assert names == {"noise-0", "noise-1"}
    assert nxt.next_cursor is None


def test_state_time_deployment_and_literal_like(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    paused = plane.create_flow_run("100%_done")
    plane.create_flow_run("100Xdone")
    plane._sqlite_conn.execute(
        "UPDATE flow_runs SET state = ?, created_at = ? WHERE id = ?",
        ["PAUSED", "2020-01-01T00:00:00+00:00", str(paused.run_id)],
    )
    deployment = plane.create_deployment(name="alpha-deploy", flow_name="100%_done")
    plane._sqlite_conn.execute(
        "INSERT INTO deployment_runs("
        "id, deployment_id, status, requested_parameters, resolved_parameters, "
        "flow_run_id, created_at, updated_at, started_at, finished_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            "dr-1",
            deployment["id"],
            "COMPLETED",
            "{}",
            "{}",
            str(paused.run_id),
            "2020-01-01T00:00:00+00:00",
            "2020-01-01T00:00:02+00:00",
            "2020-01-01T00:00:01+00:00",
            "2020-01-01T00:00:02+00:00",
        ],
    )

    literal = plane.list_flow_runs(q="100%_")
    assert [item["name"] for item in literal.items] == ["100%_done"]
    assert literal.items[0]["deployment_name"] == "alpha-deploy"
    assert literal.items[0]["deployment_id"] == deployment["id"]
    assert literal.items[0]["start_time"] == "2020-01-01T00:00:01+00:00"
    assert literal.items[0]["end_time"] == "2020-01-01T00:00:02+00:00"

    by_deployment = plane.list_flow_runs(q="alpha-deploy")
    assert [item["id"] for item in by_deployment.items] == [str(paused.run_id)]

    paused_only = plane.list_flow_runs(state="PAUSED")
    assert [item["name"] for item in paused_only.items] == ["100%_done"]

    early = plane.list_flow_runs(created_before="2021-01-01T00:00:00Z")
    assert [item["name"] for item in early.items] == ["100%_done"]
    recent = plane.list_flow_runs(created_after="2021-01-01T00:00:00Z")
    assert "100%_done" not in [item["name"] for item in recent.items]


def test_python_fallback_matches_active_reader(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    plane.create_flow_run("needle-flow", tags=["batch"])
    plane.create_flow_run("noise-0")
    filters = prepare_flow_run_list_filters(q="noise", limit=1)
    active = plane.list_flow_runs(q="noise", limit=1)
    items, cursor = query_flow_runs_page(
        plane._query_rows, filters, hide_archived=catalog_hide_archived()
    )
    assert [item["id"] for item in items] == [item["id"] for item in active.items]
    assert cursor == active.next_cursor


def test_flow_decorator_stores_tags(tmp_path: Path) -> None:
    plane = _plane(tmp_path)

    @flow(tags=["nightly", "prod"])
    def tagged_flow() -> int:
        return 1

    assert tagged_flow() == 1
    page = plane.list_flow_runs(q="nightly")
    assert page.items[0]["tags"] == ["nightly", "prod"]
    assert page.items[0]["flow_name"] == "tagged_flow"


def test_http_rejects_unknown_state_and_bad_time(tmp_path: Path, monkeypatch) -> None:
    plane = _plane(tmp_path)
    monkeypatch.setattr("prefect_compat.routes.flow_run_list.control_plane", plane)
    client = TestClient(app)
    unknown = client.get("/api/flow-runs", params={"state": "NOPE"})
    assert unknown.status_code == 400
    bad_time = client.get("/api/flow-runs", params={"created_after": "yesterday"})
    assert bad_time.status_code == 400
    paused = client.get("/api/flow-runs", params={"state": "PAUSED"})
    assert paused.status_code == 200
