"""Phase 0: subflow parent linkage on flow runs, task runs, and deployment runs."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from prefect_compat.runtime import SUBFLOW_MAX_DEPTH, InMemoryControlPlane


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / "subflow-linkage.jsonl"))


def test_create_linked_flow_run_inline(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    parent = plane.create_flow_run("parent")
    child = plane.create_flow_run(
        "child",
        parent_flow_run_id=parent.run_id,
        execution_mode="inline",
    )
    assert child.parent_flow_run_id == parent.run_id
    assert child.root_flow_run_id == parent.run_id
    assert child.execution_mode == "inline"
    assert child.depth == 1

    detail = plane.get_flow_run_detail(child.run_id)
    assert detail is not None
    assert detail["parent_flow_run_id"] == str(parent.run_id)
    assert detail["root_flow_run_id"] == str(parent.run_id)
    assert detail["depth"] == 1


def test_nested_flow_run_depth_and_root(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    root = plane.create_flow_run("root")
    mid = plane.create_flow_run(
        "mid", parent_flow_run_id=root.run_id, execution_mode="inline"
    )
    leaf = plane.create_flow_run(
        "leaf", parent_flow_run_id=mid.run_id, execution_mode="deployment"
    )
    assert leaf.depth == 2
    assert leaf.root_flow_run_id == root.run_id
    assert mid.depth == 1
    assert mid.root_flow_run_id == root.run_id


def test_subflow_depth_limit(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    parent_id = plane.create_flow_run("root").run_id
    for i in range(SUBFLOW_MAX_DEPTH):
        child = plane.create_flow_run(f"level-{i}", parent_flow_run_id=parent_id)
        parent_id = child.run_id
    with pytest.raises(ValueError, match="subflow depth exceeds maximum"):
        plane.create_flow_run("too-deep", parent_flow_run_id=parent_id)


def test_subflow_task_run_kind_and_child_refs(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    parent = plane.create_flow_run("parent")
    child_flow_id = uuid4()
    dep_run_id = uuid4()
    task = plane.create_task_run(
        parent.run_id,
        "child_subflow",
        kind="subflow",
        child_flow_run_id=child_flow_id,
        child_deployment_run_id=dep_run_id,
    )
    assert task.kind == "subflow"
    assert task.child_flow_run_id == child_flow_id
    assert task.child_deployment_run_id == dep_run_id

    listed = plane.list_task_runs(parent.run_id)
    assert listed.items[0]["kind"] == "subflow"
    assert listed.items[0]["child_flow_run_id"] == str(child_flow_id)


def test_trigger_deployment_run_parent_linkage(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    parent_flow = plane.create_flow_run("parent")
    parent_task = plane.create_task_run(
        parent_flow.run_id, "launch_child", kind="subflow"
    )
    dep = plane.create_deployment(
        name="child-deploy",
        flow_name="child_flow",
        default_parameters={"n": 1},
        paused=False,
    )
    parent_dep_run_id = uuid4()
    run = plane.trigger_deployment_run(
        UUID(dep["id"]),
        parameters={"n": 2},
        parent_flow_run_id=parent_flow.run_id,
        parent_task_run_id=parent_task.task_run_id,
        parent_deployment_run_id=parent_dep_run_id,
    )
    assert run["parent_flow_run_id"] == str(parent_flow.run_id)
    assert run["parent_task_run_id"] == str(parent_task.task_run_id)
    assert run["parent_deployment_run_id"] == str(parent_dep_run_id)
