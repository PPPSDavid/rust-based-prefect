#!/usr/bin/env python3
"""Seed a flow run with persisted JSON task results for UI visual checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--history",
        default=str(Path("data") / "persist_result_ui_history.jsonl"),
        help="Control-plane history path (server must use the same IRONFLOW_HISTORY_PATH)",
    )
    args = parser.parse_args()
    history = Path(args.history)
    history.parent.mkdir(parents=True, exist_ok=True)

    plane = InMemoryControlPlane(history_path=str(history))
    set_control_plane(plane)

    @task
    def setup() -> None:
        return None

    @task(persist_result=True)
    def expensive(x: int) -> dict:
        return {"x": x, "n": 42, "items": [1, 2, 3]}

    @task
    def volatile(x: int) -> int:
        return x + 1

    @flow(name="persist_result_demo")
    def pipeline(x: int = 7) -> int:
        setup.submit()
        payload = expensive.submit(x)
        return volatile.submit(payload.result()["n"]).result()

    result = pipeline(7)
    assert result == 43
    run = plane.latest_flow()
    assert run is not None
    arts = plane.list_artifacts_for_flow(run.run_id)
    print(
        json.dumps(
            {
                "flow_run_id": str(run.run_id),
                "flow_name": run.name,
                "state": run.state.value,
                "artifact_count": len(arts),
                "history": str(history),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
