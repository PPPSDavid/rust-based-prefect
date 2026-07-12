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


def test_custom_task_name_aligns_forecast_and_planned_nodes(tmp_path: Path) -> None:
    history = tmp_path / "custom-name-history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    set_control_plane(plane)

    @task(name="status-update")
    def notify(msg: str) -> str:
        return msg

    @flow
    def notify_flow() -> str:
        notify.submit("start")
        return notify.submit("end").result()

    assert notify_flow() == "end"
    run = plane.latest_flow()
    assert run is not None

    rows = sorted(
        plane.list_task_runs(run.run_id).items,
        key=lambda row: row["planned_node_id"] or "",
    )
    assert [row["task_name"] for row in rows] == ["status-update", "status-update"]
    assert [row["planned_node_id"] for row in rows] == ["n1", "n2"]

    dag = plane.get_flow_run_dag(run.run_id, mode="logical")
    assert dag["source"] == "forecast"
    assert [node["label"] for node in dag["nodes"]] == ["status-update-0", "status-update-1"]


def test_distinct_wrappers_same_function_are_separate_tasks(tmp_path: Path) -> None:
    history = tmp_path / "alias-task-history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    set_control_plane(plane)

    def ping_body() -> str:
        return "pong"

    start_ping = task(name="ping-start")(ping_body)
    end_ping = task(name="ping-end")(ping_body)

    @flow
    def ping_flow() -> list[str]:
        a = start_ping.submit()
        b = end_ping.submit(wait_for=[a])
        return [a.result(), b.result()]

    assert ping_flow() == ["pong", "pong"]
    latest = plane.latest_flow()
    assert latest is not None
    dag = plane.get_flow_run_dag(latest.run_id, mode="logical")
    labels = [node["label"] for node in dag["nodes"]]
    assert labels == ["ping-start-0", "ping-end-0"]
