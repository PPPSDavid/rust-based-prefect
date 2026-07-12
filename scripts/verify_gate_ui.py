#!/usr/bin/env python3
"""Smoke-check gate task DAG via API after running gated_flow."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-shim" / "src"))

from prefect_compat.server import app, control_plane, gated_flow  # noqa: E402


def main() -> int:
    gated_flow(2)
    parent_run = next(
        f for f in control_plane._flows.values() if f.name == "gated_flow"
    )
    client = TestClient(app)
    dag = client.get(f"/api/flow-runs/{parent_run.run_id}/dag?mode=expanded")
    if dag.status_code != 200:
        print(f"DAG fetch failed: {dag.status_code}", file=sys.stderr)
        return 1
    payload = dag.json()
    gate_nodes = [n for n in payload["nodes"] if n.get("kind") == "gate_task"]
    if not gate_nodes:
        print("No gate_task nodes in expanded DAG", file=sys.stderr)
        print(json.dumps(payload, indent=2)[:2000], file=sys.stderr)
        return 1
    print(json.dumps({"run_id": str(parent_run.run_id), "gate_nodes": gate_nodes}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
