from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

from prefect_compat import InMemoryControlPlane, RunState


def run_stress(total_runs: int = 1000) -> dict:
    plane = InMemoryControlPlane()
    start = time.perf_counter()

    for i in range(total_runs):
        flow = plane.create_flow_run(f"load-{i}")
        plane.set_flow_state(flow.run_id, RunState.PENDING, uuid4(), "propose", expected_version=0)
        plane.set_flow_state(flow.run_id, RunState.RUNNING, uuid4(), "start", expected_version=1)
        plane.set_flow_state(
            flow.run_id, RunState.COMPLETED, uuid4(), "complete", expected_version=2
        )

    elapsed = time.perf_counter() - start
    transitions = total_runs * 3
    return {
        "total_runs": total_runs,
        "total_transitions": transitions,
        "elapsed_seconds": elapsed,
        "transitions_per_second": transitions / elapsed if elapsed > 0 else 0,
    }


def main() -> None:
    result = run_stress(2000)
    out_path = Path("docs") / "stress_results.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
