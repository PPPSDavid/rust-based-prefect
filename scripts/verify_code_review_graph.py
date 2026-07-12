#!/usr/bin/env python3
"""Verify code-review-graph is actually usable via our MCP launcher.

Catches false-healthy setups:
  - package installed but graph DB empty/stale
  - launcher starts but tools error / return empty payloads
  - docs using non-suffixed tool names (MCP exposes ``*_tool``)

Usage:
  python3 scripts/verify_code_review_graph.py
  python3 scripts/verify_code_review_graph.py --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

# Logical names used in AGENTS.md → actual FastMCP tool names (CRG 2.3.x).
TOOL_ALIASES: dict[str, str] = {
    "list_graph_stats": "list_graph_stats_tool",
    "get_architecture_overview": "get_architecture_overview_tool",
    "semantic_search_nodes": "semantic_search_nodes_tool",
    "query_graph": "query_graph_tool",
    "get_impact_radius": "get_impact_radius_tool",
    "detect_changes": "detect_changes_tool",
}


def _fail(msg: str) -> None:
    print(f"FAIL {msg}")


def _ok(msg: str) -> None:
    print(f"OK   {msg}")


def _warn(msg: str) -> None:
    print(f"WARN {msg}")


async def _run_mcp_checks() -> tuple[list[str], dict[str, Any]]:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        return [f"mcp client not importable: {exc}"], {}

    launcher = ROOT / "tools" / "dev" / "crg_mcp_serve.py"
    if not launcher.is_file():
        return [f"missing launcher: {launcher}"], {}

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(launcher)],
        cwd=str(ROOT),
        env={**os.environ, "TOKENIZERS_PARALLELISM": "false"},
    )

    report: dict[str, Any] = {"tools": [], "checks": {}}
    issues: list[str] = []

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            server_name = getattr(init.serverInfo, "name", None)
            _ok(f"MCP initialize server={server_name!r}")
            if server_name != "code-review-graph":
                issues.append(f"unexpected server name: {server_name!r}")

            listed = await session.list_tools()
            names = sorted(t.name for t in listed.tools)
            report["tools"] = names
            _ok(f"list_tools count={len(names)}")

            for logical, actual in TOOL_ALIASES.items():
                if actual not in names:
                    issues.append(f"missing MCP tool {actual} (logical {logical})")
                    _fail(f"missing {actual}")
                else:
                    _ok(f"tool present {actual}")

            # Docs footgun: unsuffixed names must NOT work as tool ids.
            if "detect_changes" in names and "detect_changes_tool" in names:
                _warn("both detect_changes and detect_changes_tool registered")
            elif "detect_changes" in names:
                issues.append(
                    "MCP exposes detect_changes without _tool suffix; docs may be wrong"
                )

            async def call(
                tool: str, arguments: dict[str, Any]
            ) -> tuple[Any, bool, str]:
                result = await session.call_tool(tool, arguments)
                texts = [getattr(c, "text", str(c)) for c in result.content]
                blob = "\n".join(texts)
                is_err = bool(getattr(result, "isError", False))
                try:
                    parsed: Any = json.loads(blob)
                except json.JSONDecodeError:
                    parsed = None
                return parsed, is_err, blob

            # 1) stats — prove non-empty graph
            parsed, is_err, blob = await call("list_graph_stats_tool", {})
            nodes = (
                int((parsed or {}).get("total_nodes") or 0)
                if isinstance(parsed, dict)
                else 0
            )
            edges = (
                int((parsed or {}).get("total_edges") or 0)
                if isinstance(parsed, dict)
                else 0
            )
            emb = (
                int((parsed or {}).get("embeddings_count") or 0)
                if isinstance(parsed, dict)
                else 0
            )
            ok = (not is_err) and isinstance(parsed, dict) and nodes > 50 and edges > 50
            report["checks"]["list_graph_stats"] = {
                "ok": ok,
                "nodes": nodes,
                "edges": edges,
                "embeddings": emb,
            }
            if ok:
                _ok(
                    f"list_graph_stats_tool nodes={nodes} edges={edges} embeddings={emb}"
                )
                if emb == 0:
                    _warn(
                        "embeddings_count=0 (expected on Cloud core install; "
                        "keyword/hybrid search still works)"
                    )
            else:
                issues.append("list_graph_stats_tool returned empty/error graph")
                _fail(f"list_graph_stats_tool is_err={is_err} preview={blob[:160]}")

            # 2) architecture
            parsed, is_err, blob = await call("get_architecture_overview_tool", {})
            communities = (
                (parsed or {}).get("communities") if isinstance(parsed, dict) else None
            )
            ok = (not is_err) and isinstance(parsed, dict) and bool(communities)
            report["checks"]["architecture"] = {
                "ok": ok,
                "community_count": len(communities or []),
            }
            if ok:
                _ok(
                    f"get_architecture_overview_tool communities={len(communities or [])}"
                )
            else:
                issues.append("get_architecture_overview_tool empty/error")
                _fail(f"architecture is_err={is_err} preview={blob[:160]}")

            # 3) search — must find a known IronFlow symbol without embeddings
            parsed, is_err, blob = await call(
                "semantic_search_nodes_tool",
                {"query": "TransitionHookSpec"},
            )
            results = (
                (parsed or {}).get("results") if isinstance(parsed, dict) else None
            )
            ok = (not is_err) and isinstance(results, list) and len(results) > 0
            top = results[0] if results else {}
            report["checks"]["semantic_search"] = {
                "ok": ok,
                "n": len(results or []),
                "top": top.get("qualified_name") if isinstance(top, dict) else None,
            }
            if ok:
                _ok(
                    "semantic_search_nodes_tool hits="
                    f"{len(results)} top={top.get('name')} file={top.get('file_path')}"
                )
            else:
                issues.append(
                    "semantic_search_nodes_tool returned no hits for TransitionHookSpec"
                )
                _fail(f"search is_err={is_err} preview={blob[:160]}")

            # 4) query_graph callees
            parsed, is_err, blob = await call(
                "query_graph_tool",
                {
                    "pattern": "callees_of",
                    "target": str(ROOT / "scripts" / "agent_preflight.py") + "::main",
                },
            )
            ok = (
                (not is_err)
                and isinstance(parsed, dict)
                and parsed.get("status")
                in (
                    "ok",
                    "ambiguous",
                )
            )
            report["checks"]["query_graph"] = {
                "ok": ok,
                "status": (parsed or {}).get("status")
                if isinstance(parsed, dict)
                else None,
            }
            if ok:
                _ok(f"query_graph_tool status={parsed.get('status')}")
            else:
                issues.append("query_graph_tool failed")
                _fail(f"query_graph is_err={is_err} preview={blob[:160]}")

            # 5) impact radius on known files
            parsed, is_err, blob = await call(
                "get_impact_radius_tool",
                {
                    "changed_files": [
                        "tools/dev/crg_mcp_serve.py",
                        "scripts/setup_code_review_graph.sh",
                    ]
                },
            )
            total = (
                int((parsed or {}).get("total_impacted") or 0)
                if isinstance(parsed, dict)
                else 0
            )
            ok = (
                (not is_err)
                and isinstance(parsed, dict)
                and parsed.get("status") == "ok"
                and total > 0
            )
            report["checks"]["impact_radius"] = {"ok": ok, "total_impacted": total}
            if ok:
                _ok(f"get_impact_radius_tool total_impacted={total}")
            else:
                issues.append("get_impact_radius_tool empty/error")
                _fail(f"impact is_err={is_err} preview={blob[:160]}")

            # 6) detect_changes should see *something* on a dirty worktree OR still return ok
            parsed, is_err, blob = await call("detect_changes_tool", {})
            ok = (
                (not is_err)
                and isinstance(parsed, dict)
                and parsed.get("status") == "ok"
            )
            report["checks"]["detect_changes"] = {
                "ok": ok,
                "changed_files": len((parsed or {}).get("changed_files") or [])
                if isinstance(parsed, dict)
                else 0,
                "risk_score": (parsed or {}).get("risk_score")
                if isinstance(parsed, dict)
                else None,
            }
            if ok:
                _ok(
                    "detect_changes_tool files="
                    f"{report['checks']['detect_changes']['changed_files']} "
                    f"risk={report['checks']['detect_changes']['risk_score']}"
                )
            else:
                issues.append("detect_changes_tool failed")
                _fail(f"detect_changes is_err={is_err} preview={blob[:160]}")

            # 7) wrong name must error — proves we are talking to a real tool registry
            parsed, is_err, blob = await call("detect_changes", {})
            ok = is_err or "Unknown tool" in blob
            report["checks"]["wrong_name_rejected"] = {"ok": ok}
            if ok:
                _ok("unsuffixed detect_changes correctly rejected by MCP")
            else:
                issues.append("unsuffixed detect_changes unexpectedly succeeded")
                _fail("wrong-name probe did not fail")

    return issues, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable report JSON"
    )
    args = parser.parse_args()

    # Preflight import / DB before spending time on MCP.
    try:
        import code_review_graph  # noqa: F401
    except ImportError as exc:
        _fail(f"code-review-graph not installed: {exc}")
        _fail("Run: bash scripts/setup_code_review_graph.sh")
        return 1

    graph_dir = ROOT / ".code-review-graph"
    if not graph_dir.exists():
        _fail("missing .code-review-graph/ — run setup script / build first")
        return 1
    _ok("package importable and graph directory present")

    issues, report = asyncio.run(_run_mcp_checks())
    if args.json:
        print(
            json.dumps({"ok": not issues, "issues": issues, "report": report}, indent=2)
        )

    if issues:
        print("\nVerification FAILED:")
        for item in issues:
            print(f"  - {item}")
        return 2

    print(
        "\nVerification PASSED — MCP launcher answers real tool calls with non-empty graph data."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
