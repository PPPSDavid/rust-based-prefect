from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from prefect_compat import InMemoryControlPlane, RunState


def test_history_persists_across_restart(tmp_path: Path):
    history = tmp_path / "history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    run = plane.create_flow_run("persisted-flow")
    plane.set_flow_state(run.run_id, RunState.PENDING, uuid4(), "propose", expected_version=0)
    plane.set_flow_state(run.run_id, RunState.RUNNING, uuid4(), "start", expected_version=1)
    plane.set_flow_state(run.run_id, RunState.COMPLETED, uuid4(), "complete", expected_version=2)

    reloaded = InMemoryControlPlane(history_path=str(history))
    loaded_run = reloaded.get_flow(run.run_id)
    assert loaded_run.state == RunState.COMPLETED
    assert loaded_run.version == 3
    assert reloaded.summary()["flow_runs"] == 1
    assert reloaded.summary()["events"] >= 3
