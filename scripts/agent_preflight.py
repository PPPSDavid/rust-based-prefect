#!/usr/bin/env python3
"""Lightweight preflight checks for agents before push/PR.

Usage:
  python3 scripts/agent_preflight.py
  python3 scripts/agent_preflight.py --strict-crg
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ok(msg: str) -> None:
    print(f"OK  {msg}")


def _warn(msg: str) -> None:
    print(f"WARN {msg}")


def _fail(msg: str) -> None:
    print(f"FAIL {msg}")


def check_branch() -> list[str]:
    issues: list[str] = []
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    branch = (proc.stdout or "").strip()
    if branch in ("main", "master"):
        issues.append(
            f"on protected branch '{branch}' — create cursor/<desc>-SUFFIX first"
        )
        _fail(f"branch={branch}")
    else:
        _ok(f"branch={branch}")
    return issues


def check_mcp_config() -> list[str]:
    issues: list[str] = []
    path = ROOT / ".cursor" / "mcp.json"
    if not path.is_file():
        issues.append("missing .cursor/mcp.json")
        _fail("mcp.json missing")
        return issues
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        issues.append(f"invalid mcp.json: {exc}")
        _fail("mcp.json invalid JSON")
        return issues
    server = (data.get("mcpServers") or {}).get("code-review-graph") or {}
    command = str(server.get("command") or "")
    args = server.get("args") or []
    blob = " ".join([command, *map(str, args)])
    if "powershell" in blob.lower() and "crg_mcp_serve.py" not in blob:
        issues.append(
            "mcp.json still Windows PowerShell-only; use tools/dev/crg_mcp_serve.py"
        )
        _fail("mcp.json not cross-platform")
    elif "crg_mcp_serve.py" in blob or "code_review_graph" in blob:
        _ok("mcp.json points at portable CRG launcher")
    else:
        _warn("mcp.json code-review-graph entry looks unexpected")
    return issues


def check_crg(strict: bool) -> list[str]:
    issues: list[str] = []
    try:
        import code_review_graph  # noqa: F401
    except ImportError:
        msg = "code-review-graph not installed (run bash scripts/setup_code_review_graph.sh)"
        if strict:
            issues.append(msg)
            _fail(msg)
        else:
            _warn(msg)
        return issues
    _ok("code-review-graph importable")
    graph_dir = ROOT / ".code-review-graph"
    if not graph_dir.exists():
        msg = "graph DB missing — run bash scripts/setup_code_review_graph.sh"
        if strict:
            issues.append(msg)
            _fail(msg)
        else:
            _warn(msg)
    else:
        _ok("graph DB directory present")
    return issues


def check_perf_artifacts_dirty() -> list[str]:
    issues: list[str] = []
    proc = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--",
            "docs/perf_matrix_results.json",
            "docs/perf_matrix_summary.md",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = (proc.stdout or "").strip()
    if dirty:
        _warn(
            "perf_matrix default outputs are modified — revert unless this PR updates baselines:\n"
            + dirty
        )
    else:
        _ok("perf_matrix default doc artifacts clean")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict-crg",
        action="store_true",
        help="Fail if code-review-graph or graph DB is missing",
    )
    parser.add_argument(
        "--verify-mcp",
        action="store_true",
        help="Also run scripts/verify_code_review_graph.py (stdio MCP demo calls)",
    )
    args = parser.parse_args()

    issues: list[str] = []
    issues.extend(check_branch())
    issues.extend(check_mcp_config())
    issues.extend(check_crg(strict=args.strict_crg))
    issues.extend(check_perf_artifacts_dirty())

    if args.verify_mcp:
        print("--- MCP verify ---")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "verify_code_review_graph.py")],
            cwd=ROOT,
            check=False,
        )
        if proc.returncode != 0:
            issues.append("verify_code_review_graph.py failed")

    if issues:
        print("\nPreflight found issues:")
        for item in issues:
            print(f"  - {item}")
        return 1
    print("\nPreflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
