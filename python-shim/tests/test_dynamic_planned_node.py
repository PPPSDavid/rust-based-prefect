from __future__ import annotations

from pathlib import Path

from prefect_compat import InMemoryControlPlane, set_control_plane, task


@task
def status(message: str) -> str:
    return message


def test_dynamic_planned_node_when_no_manifest_nodes(tmp_path: Path) -> None:
    history = tmp_path / "dynamic-planned-history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    set_control_plane(plane)

    run = plane.create_flow_run("manual")
    first = plane.next_planned_node_id(run.run_id, "status")
    second = plane.next_planned_node_id(run.run_id, "status")

    assert first == "dyn_status_0"
    assert second == "dyn_status_1"
