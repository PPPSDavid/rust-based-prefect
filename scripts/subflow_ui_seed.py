"""Seed inline + deployment subflow runs into persisted history for UI visual checks.

Writes JSONL to FLOWOXIDE_HISTORY_PATH and the companion SQLite sidecar (.db) used by
the API query path. The server must use the same FLOWOXIDE_HISTORY_PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from pathlib import Path
from typing import cast

from prefect_compat import (
    InMemoryControlPlane,
    deployment_ref,
    flow,
    set_control_plane,
    task,
)
from prefect_compat.decorators import TaskWrapper
from prefect_compat.worker import run_worker_loop


def _plane(history: Path) -> InMemoryControlPlane:
    history.parent.mkdir(parents=True, exist_ok=True)
    return InMemoryControlPlane(history_path=str(history))


def seed(history_path: Path) -> dict[str, str]:
    plane = _plane(history_path)
    set_control_plane(plane)
    registry: dict = {}
    stop = threading.Event()

    @task
    def leaf_task(n: int) -> int:
        return n + 1

    @flow
    def leaf_flow(n: int) -> int:
        return cast(TaskWrapper[int], leaf_task).submit(n).result()

    @flow
    def child_inline(n: int) -> int:
        return leaf_flow(n)

    @flow
    def parent_inline() -> int:
        return child_inline(10)

    @flow
    def child_deploy(n: int = 0) -> int:
        return 42 + n

    @flow
    def parent_deploy() -> int:
        fut = deployment_ref("subflow-child-deploy").submit()
        return fut.result()

    @flow
    def parent_mixed() -> int:
        inline_val = child_inline(3)
        fut = deployment_ref("subflow-child-deploy").submit(n=inline_val)
        return fut.result()

    registry["leaf_flow"] = leaf_flow
    registry["child_inline"] = child_inline
    registry["parent_inline"] = parent_inline
    registry["child_deploy"] = child_deploy
    registry["parent_deploy"] = parent_deploy
    registry["parent_mixed"] = parent_mixed

    plane.create_deployment(
        name="subflow-child-deploy",
        flow_name="child_deploy",
        default_parameters={},
        paused=False,
    )

    worker = threading.Thread(
        target=run_worker_loop,
        kwargs={
            "control_plane": plane,
            "worker_name": "subflow-seed-worker",
            "work_pool_id": "default-process-pool",
            "flow_registry": registry,
            "lease_seconds": 60,
            "stop_event": stop,
        },
        daemon=True,
    )
    worker.start()
    try:
        parent_inline()
        parent_deploy()
        parent_mixed()
    finally:
        stop.set()
        worker.join(timeout=10)

    runs = {
        "parent_inline": next(
            f for f in plane._flows.values() if f.name == "parent_inline"
        ),
        "parent_deploy": next(
            f for f in plane._flows.values() if f.name == "parent_deploy"
        ),
        "parent_mixed": next(
            f for f in plane._flows.values() if f.name == "parent_mixed"
        ),
        "child_inline": next(
            f for f in plane._flows.values() if f.name == "child_inline"
        ),
    }
    out = {k: str(v.run_id) for k, v in runs.items()}
    for name, rid in out.items():
        detail = plane.get_flow_run_detail(runs[name].run_id) or {}
        dag = plane.get_flow_run_dag(runs[name].run_id, mode="logical")
        kinds = sorted({n.get("kind", "task") for n in dag["nodes"]})
        print(f"{name}: run_id={rid} state={detail.get('state')} dag_kinds={kinds}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed subflow demo runs for UI inspection."
    )
    parser.add_argument(
        "--history-path",
        default=os.getenv(
            "FLOWOXIDE_HISTORY_PATH", str(Path("data") / "flowoxide_history.jsonl")
        ),
    )
    args = parser.parse_args()
    history = Path(args.history_path)
    if history.exists():
        history.unlink()
    ids = seed(history)
    print(json.dumps({"seeded_runs": ids}, indent=2))
    print(
        "Start server + UI, then open run detail → DAG tab for each parent run above."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
