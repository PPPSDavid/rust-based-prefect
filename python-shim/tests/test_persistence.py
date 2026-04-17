from __future__ import annotations

import gc
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


def test_sqlite_read_model_is_rebuilt_from_history_if_missing(tmp_path: Path):
    history = tmp_path / "history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    run = plane.create_flow_run("persisted-flow")
    plane.set_flow_state(run.run_id, RunState.PENDING, uuid4(), "propose", expected_version=0)
    plane.set_flow_state(run.run_id, RunState.RUNNING, uuid4(), "start", expected_version=1)
    plane.set_flow_state(run.run_id, RunState.COMPLETED, uuid4(), "complete", expected_version=2)

    db_path = history.with_suffix(".db")
    assert db_path.exists()
    del plane
    gc.collect()
    db_path.unlink()

    reloaded = InMemoryControlPlane(history_path=str(history))
    runs_page = reloaded.list_flow_runs(limit=10)
    assert len(runs_page.items) >= 1
    assert runs_page.items[0]["id"] == str(run.run_id)
