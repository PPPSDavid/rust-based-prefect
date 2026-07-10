"""Deployment-backed subflows (mechanism 2): subflow as task via deployment enqueue."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Sequence, TypeVar
from uuid import UUID

from .decorators import (
    _ACTIVE_DEPLOYMENT_RUN,
    _ACTIVE_FLOW_RUN,
    TaskFuture,
    wait,
)
from .runtime import RunState

T = TypeVar("T")
_UNSET = object()


def _control_plane():
    from .decorators import _CONTROL_PLANE

    return _CONTROL_PLANE


@dataclass
class SubflowFuture(Generic[T]):
    deployment_run_id: str
    parent_task_run_id: str
    planned_node_id: str | None = None
    child_flow_run_id: str | None = None
    _value: Any = _UNSET

    def result(self) -> T:
        if self._value is not _UNSET:
            return self._value  # type: ignore[return-value]

        dep_token = _ACTIVE_DEPLOYMENT_RUN.set(None)
        try:
            terminal = _control_plane().wait_for_deployment_run_terminal(
                UUID(self.deployment_run_id),
                parent_task_run_id=UUID(self.parent_task_run_id),
            )
        finally:
            _ACTIVE_DEPLOYMENT_RUN.reset(dep_token)

        status = str(terminal.get("status", ""))
        flow_run_id = terminal.get("flow_run_id")
        if flow_run_id:
            self.child_flow_run_id = str(flow_run_id)

        if status == "COMPLETED":
            if flow_run_id:
                self._value = _control_plane().get_flow_result(UUID(str(flow_run_id)))
            else:
                self._value = None
            return self._value  # type: ignore[return-value]
        if status == "CANCELLED":
            raise RuntimeError(f"subflow deployment run {self.deployment_run_id} was cancelled")
        err = terminal.get("error") or f"subflow deployment run {self.deployment_run_id} failed"
        raise RuntimeError(str(err))


class DeploymentSubflowHandle:
    """Reference to a deployed flow; use ``.submit()`` for mechanism-2 subflows."""

    def __init__(self, deployment_id: UUID, name: str, flow_name: str) -> None:
        self.deployment_id = deployment_id
        self.name = name
        self.flow_name = flow_name

    def submit(
        self,
        *,
        wait_for: Sequence[TaskFuture[Any] | "SubflowFuture[Any]"] | None = None,
        **parameters: Any,
    ) -> SubflowFuture[Any]:
        if wait_for:
            wait(wait_for)

        flow_run_id = _ACTIVE_FLOW_RUN.get()
        if flow_run_id is None:
            raise RuntimeError(
                "deployment subflow submit() requires an active parent flow run; "
                "call from inside a @flow function"
            )

        plane = _control_plane()
        task_name = f"subflow:{self.name}"
        planned_node_id = plane.next_planned_node_id(flow_run_id, task_name)
        task_run = plane.create_task_run(
            flow_run_id,
            task_name,
            planned_node_id=planned_node_id,
            kind="subflow",
        )
        plane.record_task_event(task_run.task_run_id, "task_pending", {"subflow": self.name})

        parent_dep_run_id = _ACTIVE_DEPLOYMENT_RUN.get()
        dep_run = plane.trigger_deployment_run(
            self.deployment_id,
            parameters=parameters,
            parent_flow_run_id=flow_run_id,
            parent_task_run_id=task_run.task_run_id,
            parent_deployment_run_id=parent_dep_run_id,
        )
        dep_run_id = UUID(str(dep_run["id"]))
        plane.update_subflow_task_linkage(
            task_run.task_run_id,
            child_deployment_run_id=dep_run_id,
        )

        plane.record_task_event(
            task_run.task_run_id,
            "task_running",
            {"subflow": self.name, "deployment_run_id": str(dep_run_id)},
        )

        return SubflowFuture(
            deployment_run_id=str(dep_run_id),
            parent_task_run_id=str(task_run.task_run_id),
            planned_node_id=planned_node_id,
        )


def deployment_ref(name_or_id: str | UUID) -> DeploymentSubflowHandle:
    """Resolve a deployment by unique name or UUID string for subflow-as-task submit."""
    if isinstance(name_or_id, UUID):
        dep = _control_plane().get_deployment(name_or_id)
    elif _looks_like_uuid(name_or_id):
        dep = _control_plane().get_deployment(UUID(name_or_id))
    else:
        dep = _control_plane().get_deployment_by_name(name_or_id)
    if dep is None:
        raise ValueError(f"deployment not found: {name_or_id}")
    if dep.get("paused"):
        raise ValueError(f"deployment is paused: {dep.get('name', name_or_id)}")
    return DeploymentSubflowHandle(
        deployment_id=UUID(str(dep["id"])),
        name=str(dep["name"]),
        flow_name=str(dep["flow_name"]),
    )


def _looks_like_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except ValueError:
        return False
