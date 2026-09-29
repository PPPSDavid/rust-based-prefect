from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from ironflow_mcp.client import IronFlowHttp
from ironflow_mcp.server import build_server
from prefect_compat.decorators import set_control_plane
from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.server import app, control_plane


def _swap_plane(tmp_path: Path) -> TestClient:
    history = tmp_path / "mcp-history.jsonl"
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
    return TestClient(app)


def _run(coro):  # noqa: ANN001
    return asyncio.run(coro)


def test_lists_read_only_tools(tmp_path: Path) -> None:
    http = TestClient(app)  # plane unused for list_tools
    mcp = build_server(http=IronFlowHttp(session=http))

    async def _inner() -> None:
        from fastmcp import Client

        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools}
            assert "orientation" in names
            assert "get_dashboard" in names
            assert "get_flow_run_dag" in names
            assert "get_object_schema" in names
            # no mutation tools in Phase 1
            assert "run_deployment" not in names
            assert "cancel_flow_run" not in names
            for tool in tools:
                ann = tool.annotations
                assert ann is not None
                assert ann.read_only_hint is True

    _run(_inner())


def test_orientation_and_dashboard(tmp_path: Path) -> None:
    http = _swap_plane(tmp_path)
    control_plane.create_flow_run("mcp-demo")
    mcp = build_server(http=IronFlowHttp(session=http))

    async def _inner() -> None:
        from fastmcp import Client

        async with Client(mcp) as client:
            orient = await client.call_tool("orientation", {})
            assert "read-only" in str(orient.data).lower()
            dash = await client.call_tool("get_dashboard", {})
            assert isinstance(dash.data, dict)
            assert "recent_flow_runs" in dash.data
            runs = dash.data["recent_flow_runs"]
            assert any(item.get("name") == "mcp-demo" for item in runs.get("items", []))

    _run(_inner())


def test_diagnose_failed_run_scenario(tmp_path: Path) -> None:
    """Scripted support-case eval: list runs → inspect → logs/events/dag."""
    http = _swap_plane(tmp_path)
    run = control_plane.create_flow_run("late-run")
    run_id = str(run.run_id)
    mcp = build_server(http=IronFlowHttp(session=http))

    async def _inner() -> None:
        from fastmcp import Client

        async with Client(mcp) as client:
            listed = await client.call_tool("get_flow_runs", {"limit": 10})
            assert any(item["id"] == run_id for item in listed.data["items"])
            detail = await client.call_tool("get_flow_run", {"flow_run_id": run_id})
            assert detail.data["id"] == run_id
            logs = await client.call_tool(
                "get_flow_run_logs", {"flow_run_id": run_id, "limit": 50}
            )
            assert "items" in logs.data
            events = await client.call_tool(
                "read_events", {"flow_run_id": run_id, "limit": 50}
            )
            assert "items" in events.data
            dag = await client.call_tool(
                "get_flow_run_dag",
                {"flow_run_id": run_id, "mode": "logical"},
            )
            assert isinstance(dag.data, dict)

    _run(_inner())


def test_get_object_schema_path(tmp_path: Path) -> None:
    http = _swap_plane(tmp_path)
    mcp = build_server(http=IronFlowHttp(session=http))

    async def _inner() -> None:
        from fastmcp import Client

        async with Client(mcp) as client:
            full = await client.call_tool("get_object_schema", {})
            assert "paths" in full.data or "openapi" in full.data
            one = await client.call_tool(
                "get_object_schema", {"path": "/api/flow-runs"}
            )
            assert one.data.get("path") == "/api/flow-runs"
            assert "schema" in one.data

    _run(_inner())
