"""FSM validation at the Rust boundary (skipped when the cdylib is not built)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from prefect_compat.rust_bridge import native_library_available
from prefect_compat.runtime import InMemoryControlPlane, RunState


@pytest.mark.skipif(not native_library_available(), reason="ironflow_engine cdylib not found")
def test_rust_rejects_invalid_flow_transition(tmp_path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "a.jsonl"))
    assert plane._rust_native_persistence is True
    run = plane.create_flow_run("x")
    with pytest.raises(ValueError, match="invalid transition"):
        plane.set_flow_state(run.run_id, RunState.COMPLETED, uuid4(), "bad", expected_version=0)


@pytest.mark.skipif(not native_library_available(), reason="ironflow_engine cdylib not found")
def test_rust_persists_flow_transition_to_sqlite(tmp_path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "np.jsonl"))
    run = plane.create_flow_run("native")
    plane.set_flow_state(run.run_id, RunState.PENDING, uuid4(), "propose", expected_version=0)
    detail = plane.get_flow_run_detail(run.run_id)
    assert detail is not None
    assert detail["state"] == "PENDING"


@pytest.mark.skipif(not native_library_available(), reason="ironflow_engine cdylib not found")
def test_rust_persists_flow_and_task_creation(tmp_path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "create.jsonl"))
    run = plane.create_flow_run("native-create")
    task = plane.create_task_run(run.run_id, "t1")
    flow_detail = plane.get_flow_run_detail(run.run_id)
    assert flow_detail is not None
    assert flow_detail["state"] == "SCHEDULED"
    tasks = plane.list_task_runs(run.run_id, limit=10).items
    assert any(item["id"] == str(task.task_run_id) for item in tasks)


@pytest.mark.skipif(not native_library_available(), reason="ironflow_engine cdylib not found")
def test_rust_persists_manifest_write(tmp_path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "manifest.jsonl"))
    run = plane.create_flow_run("m")
    manifest = {"nodes": [{"node_id": "n1", "task_name": "t1"}], "edges": []}
    plane.save_flow_manifest(
        run_id=run.run_id,
        manifest=manifest,
        forecast={"eta_seconds": 1},
        warnings=[],
        fallback_required=False,
        source="forecast",
    )
    dag = plane.get_flow_run_dag(run.run_id, mode="logical")
    assert dag["source"] == "forecast"
    assert any(node["task_name"] == "t1" for node in dag["nodes"])


@pytest.mark.skipif(not native_library_available(), reason="ironflow_engine cdylib not found")
def test_batch_task_events_pending_running(tmp_path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "batch.jsonl"))
    run = plane.create_flow_run("batch")
    task = plane.create_task_run(run.run_id, "t1")
    plane.record_task_events_batch(
        task.task_run_id,
        [("task_pending", None), ("task_running", None)],
    )
    refreshed = plane.get_task_run(task.task_run_id)
    assert refreshed.state == RunState.RUNNING
    assert refreshed.version == 2


@pytest.mark.skipif(not native_library_available(), reason="ironflow_engine cdylib not found")
def test_batch_flow_transitions_pending_running(tmp_path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "flow-batch.jsonl"))
    run = plane.create_flow_run("fb")
    out = plane.set_flow_states_batch(
        run.run_id,
        [
            (RunState.PENDING, uuid4(), "propose", 0),
            (RunState.RUNNING, uuid4(), "start", 1),
        ],
    )
    assert len(out) == 2
    assert out[-1].state == RunState.RUNNING
    assert plane.get_flow(run.run_id).version == 2


@pytest.mark.skipif(not native_library_available(), reason="ironflow_engine cdylib not found")
def test_rust_rejects_invalid_task_transition(tmp_path) -> None:
    plane = InMemoryControlPlane(history_path=str(tmp_path / "b.jsonl"))
    run = plane.create_flow_run("f")
    tr = plane.create_task_run(run.run_id, "t1")
    with pytest.raises(ValueError, match="invalid transition"):
        plane.record_task_event(tr.task_run_id, "task_running")


@pytest.mark.skipif(not native_library_available(), reason="ironflow_engine cdylib not found")
def test_python_legacy_fsm_when_rust_disabled(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRONFLOW_USE_RUST_FSM", "0")
    plane = InMemoryControlPlane(history_path=str(tmp_path / "c.jsonl"))
    run = plane.create_flow_run("x")
    with pytest.raises(ValueError, match="invalid transition"):
        plane.set_flow_state(run.run_id, RunState.COMPLETED, uuid4(), "bad", expected_version=0)
    monkeypatch.delenv("IRONFLOW_USE_RUST_FSM", raising=False)


@pytest.mark.skipif(not native_library_available(), reason="ironflow_engine cdylib not found")
def test_rust_control_roundtrip_register_and_transition() -> None:
    from prefect_compat.rust_bridge import RustFsmBridge

    b = RustFsmBridge()
    h = b.engine_new()
    try:
        run_id = str(uuid4())
        out = b.control(
            h,
            "register_flow",
            {"id": run_id, "name": "n", "state": "SCHEDULED", "version": 0},
        )
        assert out.get("ok") is True
        bad = b.control(
            h,
            "set_flow_state",
            {
                "run_id": run_id,
                "to_state": "COMPLETED",
                "transition_token": str(uuid4()),
                "transition_kind": "x",
                "expected_version": 0,
            },
        )
        assert bad.get("ok") is False
        assert bad.get("error", {}).get("code") == "invalid_transition"
    finally:
        b.engine_free(h)
