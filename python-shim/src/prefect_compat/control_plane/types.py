from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

SUBFLOW_MAX_DEPTH = 32
LIFECYCLE_LOG = logging.getLogger("ironflow.lifecycle")


class RunState(StrEnum):
    SCHEDULED = "SCHEDULED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class FlowRunRecord:
    run_id: UUID
    name: str
    state: RunState
    version: int
    parent_flow_run_id: UUID | None = None
    parent_task_run_id: UUID | None = None
    root_flow_run_id: UUID | None = None
    execution_mode: str | None = None
    depth: int = 0
    resume_from_flow_run_id: UUID | None = None
    resume_lineage_id: UUID | None = None
    parameters_fingerprint: str | None = None
    resume_skips_enabled: bool = False


@dataclass
class SetStateResult:
    status: str
    state: RunState
    version: int


@dataclass
class TaskRunRecord:
    task_run_id: UUID
    flow_run_id: UUID
    task_name: str
    planned_node_id: str | None
    state: RunState
    version: int
    kind: str = "task"
    child_flow_run_id: UUID | None = None
    child_deployment_run_id: UUID | None = None
    gate_open_at: str | None = None
    tags: tuple[str, ...] = ()
    contribute_to_flow_state: bool = True


@dataclass
class PageResult:
    items: list[dict[str, Any]]
    next_cursor: str | None = None


class FlowRunSchedulingHeld(RuntimeError):
    """Raised when new task runs cannot start because the flow is operator-paused."""


@dataclass
class DeploymentRecord:
    deployment_id: UUID
    name: str
    flow_name: str
    entrypoint: str | None
    path: str | None
    default_parameters: dict[str, Any]
    paused: bool


def legacy_is_valid_transition(from_state: RunState, to_state: RunState) -> bool:
    allowed: dict[RunState, set[RunState]] = {
        RunState.SCHEDULED: {RunState.PENDING, RunState.CANCELLED},
        RunState.PENDING: {RunState.RUNNING, RunState.CANCELLED},
        RunState.RUNNING: {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
            RunState.PAUSED,
        },
        RunState.PAUSED: {RunState.RUNNING, RunState.CANCELLED},
        RunState.COMPLETED: set(),
        RunState.FAILED: set(),
        RunState.CANCELLED: set(),
    }
    return to_state in allowed[from_state]
