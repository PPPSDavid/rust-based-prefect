"""FastMCP server: read-only IronFlow inspection tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import IronFlowHttp, default_api_url

_READ_ONLY_KW = {
    "read_only_hint": True,
    "idempotent_hint": True,
    "open_world_hint": False,
}


def build_server(
    *,
    http: IronFlowHttp | None = None,
    name: str = "IronFlow",
) -> FastMCP:
    """Create a FastMCP server bound to an IronFlow HTTP API client."""
    client = http or IronFlowHttp()
    mcp = FastMCP(name)

    def ro(title: str) -> dict[str, Any]:
        return {
            "annotations": ToolAnnotations(title=title, **_READ_ONLY_KW),
        }

    @mcp.tool(**ro("Orientation"))
    def orientation() -> str:
        """Summarize IronFlow MCP capabilities and mutation boundaries.

        Tools are read-only. To trigger, cancel, pause, or retry runs, use the
        `ironflow` CLI (`flow-run`, `deployment`, `api`) — see
        docs/how-to/ai-assistants.md.
        """
        return (
            "IronFlow MCP tools are read-only inspection helpers over "
            f"{client.base_url}. "
            "Use get_dashboard for a quick health overview, then "
            "get_flow_runs / get_flow_run / get_flow_run_logs / "
            "get_flow_run_dag for diagnosis. "
            "Mutations (run, cancel, pause, resume, retry) stay on the "
            "`ironflow` CLI. get_object_schema reads /openapi.json."
        )

    @mcp.tool(**ro("Server Info"))
    def get_server_info() -> dict[str, Any]:
        """Return IronFlow `/api/server-info` and `/health`."""
        health = client.get("/health")
        try:
            info = client.get("/api/server-info")
        except RuntimeError as exc:
            info = {"error": str(exc)}
        return {"api_url": client.base_url, "health": health, "server_info": info}

    @mcp.tool(**ro("Dashboard"))
    def get_dashboard() -> dict[str, Any]:
        """High-level overview: history summary, work pools, concurrency limits."""
        summary = client.get("/history/summary")
        pools = client.get("/api/work-pools")
        limits = client.get("/api/concurrency-limits")
        recent = client.get("/api/flow-runs", params={"limit": 10})
        return {
            "summary": summary,
            "work_pools": pools,
            "concurrency_limits": limits,
            "recent_flow_runs": recent,
        }

    @mcp.tool(**ro("List Flows"))
    def get_flows(status: str | None = None) -> dict[str, Any]:
        """List catalog flows (`status`: active|archived|deleted)."""
        return client.get("/api/flows", params={"status": status})

    @mcp.tool(**ro("List Deployments"))
    def get_deployments(limit: int = 50) -> dict[str, Any]:
        """List deployments."""
        return client.get("/api/deployments", params={"limit": limit})

    @mcp.tool(**ro("List Flow Runs"))
    def get_flow_runs(
        state: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List flow runs, optionally filtered by state."""
        return client.get(
            "/api/flow-runs",
            params={"state": state, "limit": limit, "cursor": cursor},
        )

    @mcp.tool(**ro("Get Flow Run"))
    def get_flow_run(flow_run_id: str) -> dict[str, Any]:
        """Get one flow run by UUID."""
        return client.get(f"/api/flow-runs/{flow_run_id}")

    @mcp.tool(**ro("List Task Runs"))
    def get_task_runs(flow_run_id: str, limit: int = 200) -> dict[str, Any]:
        """List task runs for a flow run."""
        return client.get(
            f"/api/flow-runs/{flow_run_id}/task-runs",
            params={"limit": limit},
        )

    @mcp.tool(**ro("Flow Run Logs"))
    def get_flow_run_logs(
        flow_run_id: str,
        task_run_id: str | None = None,
        level: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Retrieve logs for a flow run."""
        return client.get(
            f"/api/flow-runs/{flow_run_id}/logs",
            params={
                "task_run_id": task_run_id,
                "level": level,
                "limit": limit,
            },
        )

    @mcp.tool(**ro("Read Events"))
    def read_events(flow_run_id: str, limit: int = 500) -> dict[str, Any]:
        """List state/event history for a flow run."""
        return client.get(
            f"/api/flow-runs/{flow_run_id}/events",
            params={"limit": limit},
        )

    @mcp.tool(**ro("Flow Run DAG"))
    def get_flow_run_dag(
        flow_run_id: str,
        mode: str = "logical",
    ) -> dict[str, Any]:
        """Get the flow-run DAG (`logical` or `expanded`). IronFlow differentiator."""
        return client.get(
            f"/api/flow-runs/{flow_run_id}/dag",
            params={"mode": mode},
        )

    @mcp.tool(**ro("List Work Pools"))
    def get_work_pools() -> dict[str, Any]:
        """List work pools."""
        return client.get("/api/work-pools")

    @mcp.tool(**ro("Concurrency Limits"))
    def get_concurrency_limits() -> dict[str, Any]:
        """List global concurrency limits."""
        return client.get("/api/concurrency-limits")

    @mcp.tool(**ro("OpenAPI Schema"))
    def get_object_schema(path: str | None = None) -> dict[str, Any]:
        """Return OpenAPI schema, optionally one path (e.g. `/api/flow-runs`)."""
        schema = client.get("/openapi.json")
        if not path:
            return schema
        paths = schema.get("paths") if isinstance(schema, dict) else None
        if not isinstance(paths, dict):
            return schema
        key = path if path.startswith("/") else f"/{path}"
        match = paths.get(key)
        if match is None:
            return {"error": f"path not found: {key}", "available": sorted(paths)[:50]}
        return {"path": key, "schema": match}

    return mcp


def create_server() -> FastMCP:
    """Default server for CLI / `fastmcp run` entrypoints."""
    return build_server(http=IronFlowHttp(base_url=default_api_url()))
