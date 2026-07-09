from __future__ import annotations

from pathlib import Path

from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task


@task
def status(message: str) -> str:
    return message


@task
def work(x: int) -> int:
    return x + 1


@flow
def status_bookend_flow() -> str:
    started = status.submit("start")
    middle = work.submit(1, wait_for=[started])
    ended = status.submit("end", wait_for=[middle])
    return ended.result()


def test_repeated_task_name_gets_distinct_planned_nodes_and_dag_nodes(tmp_path: Path) -> None:
    history = tmp_path / "repeat-task-history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    set_control_plane(plane)

    result = status_bookend_flow()
    assert result == "end"

    run = plane.latest_flow()
    assert run is not None

    task_rows = plane.list_task_runs(run.run_id).items
    status_rows = [row for row in task_rows if row["task_name"] == "status"]
    assert len(status_rows) == 2
    assert status_rows[0]["planned_node_id"] != status_rows[1]["planned_node_id"]

    dag = plane.get_flow_run_dag(run.run_id, mode="logical")
    assert dag["source"] == "forecast"
    labels = [node["label"] for node in dag["nodes"]]
    assert "status-0" in labels
    assert "status-1" in labels
    assert len([node for node in dag["nodes"] if node["task_name"] == "status"]) == 2


def test_repeated_expr_submits_get_distinct_manifest_planned_nodes(tmp_path: Path) -> None:
    history = tmp_path / "dynamic-planned-history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    set_control_plane(plane)

    @flow
    def ad_hoc() -> None:
        status.submit("a")
        status.submit("b")

    ad_hoc()
    run = plane.latest_flow()
    assert run is not None
    rows = plane.list_task_runs(run.run_id).items
    planned = sorted([row["planned_node_id"] for row in rows if row["task_name"] == "status"])
    assert planned == ["n1", "n2"]
