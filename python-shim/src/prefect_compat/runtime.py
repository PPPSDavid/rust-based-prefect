from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID, uuid4


class RunState(str, Enum):
    SCHEDULED = "SCHEDULED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class FlowRunRecord:
    run_id: UUID
    name: str
    state: RunState
    version: int


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
    state: RunState
    version: int


class InMemoryControlPlane:
    """
    Python MVP control plane with the same transition semantics as the Rust engine.
    This is used by the shim and can be swapped for an HTTP Rust facade.
    """

    def __init__(self, history_path: str | None = None) -> None:
        self._flows: dict[UUID, FlowRunRecord] = {}
        self._tasks: dict[UUID, TaskRunRecord] = {}
        self._events: list[dict[str, Any]] = []
        self._tokens: set[UUID] = set()
        self._lock = Lock()
        self._latest_flow_run_id: UUID | None = None
        self._history_path = Path(history_path) if history_path else None
        if self._history_path is not None:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._load_from_history()

    def create_flow_run(self, name: str) -> FlowRunRecord:
        record = FlowRunRecord(run_id=uuid4(), name=name, state=RunState.SCHEDULED, version=0)
        with self._lock:
            self._flows[record.run_id] = record
            self._latest_flow_run_id = record.run_id
            self._persist_record(
                {
                    "record_type": "flow_create",
                    "run_id": str(record.run_id),
                    "name": record.name,
                    "state": record.state.value,
                    "version": record.version,
                }
            )
        return record

    def create_task_run(self, flow_run_id: UUID, task_name: str) -> TaskRunRecord:
        task = TaskRunRecord(
            task_run_id=uuid4(),
            flow_run_id=flow_run_id,
            task_name=task_name,
            state=RunState.SCHEDULED,
            version=0,
        )
        with self._lock:
            self._tasks[task.task_run_id] = task
            self._persist_record(
                {
                    "record_type": "task_create",
                    "task_run_id": str(task.task_run_id),
                    "flow_run_id": str(task.flow_run_id),
                    "task_name": task.task_name,
                    "state": task.state.value,
                    "version": task.version,
                }
            )
        return task

    def set_flow_state(
        self,
        run_id: UUID,
        to_state: RunState,
        transition_token: UUID,
        transition_kind: str,
        expected_version: int | None = None,
    ) -> SetStateResult:
        with self._lock:
            record = self._flows[run_id]

            if transition_token in self._tokens:
                return SetStateResult(status="duplicate", state=record.state, version=record.version)

            if expected_version is not None and expected_version != record.version:
                raise ValueError(
                    f"version conflict expected={expected_version} actual={record.version}"
                )

            if not _is_valid_transition(record.state, to_state):
                raise ValueError(f"invalid transition {record.state} -> {to_state}")

            self._tokens.add(transition_token)
            self._events.append(
                {
                    "event_id": str(uuid4()),
                    "run_id": str(run_id),
                    "from_state": record.state.value,
                    "to_state": to_state.value,
                    "kind": transition_kind,
                }
            )

            record.state = to_state
            record.version += 1
            self._persist_record(
                {
                    "record_type": "flow_transition",
                    "run_id": str(run_id),
                    "to_state": to_state.value,
                    "kind": transition_kind,
                    "version": record.version,
                    "transition_token": str(transition_token),
                }
            )
            return SetStateResult(status="applied", state=record.state, version=record.version)

    def get_flow(self, run_id: UUID) -> FlowRunRecord:
        with self._lock:
            return self._flows[run_id]

    def latest_flow(self) -> FlowRunRecord | None:
        with self._lock:
            if self._latest_flow_run_id is None:
                return None
            return self._flows[self._latest_flow_run_id]

    def record_task_event(self, task_run_id: UUID, event_type: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            task = self._tasks[task_run_id]
            if event_type == "task_pending":
                task.state = RunState.PENDING
                task.version += 1
            elif event_type == "task_running":
                task.state = RunState.RUNNING
                task.version += 1
            elif event_type == "task_completed":
                task.state = RunState.COMPLETED
                task.version += 1
            elif event_type == "task_failed":
                task.state = RunState.FAILED
                task.version += 1

            self._events.append(
                {
                    "event_id": str(uuid4()),
                    "run_id": str(task.flow_run_id),
                    "task_run_id": str(task_run_id),
                    "event_type": event_type,
                    "data": data or {},
                }
            )
            self._persist_record(
                {
                    "record_type": "task_event",
                    "task_run_id": str(task_run_id),
                    "flow_run_id": str(task.flow_run_id),
                    "event_type": event_type,
                    "state": task.state.value,
                    "version": task.version,
                    "data": data or {},
                }
            )

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def summary(self) -> dict[str, int]:
        with self._lock:
            return {
                "flow_runs": len(self._flows),
                "task_runs": len(self._tasks),
                "events": len(self._events),
            }

    def _persist_record(self, record: dict[str, Any]) -> None:
        if self._history_path is None:
            return
        with self._history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record))
            f.write("\n")

    def _load_from_history(self) -> None:
        if self._history_path is None or not self._history_path.exists():
            return
        for line in self._history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            self._apply_record(rec)

    def _apply_record(self, rec: dict[str, Any]) -> None:
        record_type = rec.get("record_type")
        if record_type == "flow_create":
            run_id = UUID(rec["run_id"])
            flow = FlowRunRecord(
                run_id=run_id,
                name=rec["name"],
                state=RunState(rec["state"]),
                version=int(rec["version"]),
            )
            self._flows[run_id] = flow
            self._latest_flow_run_id = run_id
        elif record_type == "task_create":
            task_id = UUID(rec["task_run_id"])
            task = TaskRunRecord(
                task_run_id=task_id,
                flow_run_id=UUID(rec["flow_run_id"]),
                task_name=rec["task_name"],
                state=RunState(rec["state"]),
                version=int(rec["version"]),
            )
            self._tasks[task_id] = task
        elif record_type == "flow_transition":
            run_id = UUID(rec["run_id"])
            if run_id in self._flows:
                flow = self._flows[run_id]
                flow.state = RunState(rec["to_state"])
                flow.version = int(rec["version"])
            token = rec.get("transition_token")
            if token:
                self._tokens.add(UUID(token))
        elif record_type == "task_event":
            task_id = UUID(rec["task_run_id"])
            if task_id in self._tasks:
                task = self._tasks[task_id]
                task.state = RunState(rec["state"])
                task.version = int(rec["version"])

        if record_type in {"flow_transition", "task_event"}:
            # Rebuild in-memory event stream from persisted history.
            self._events.append(rec)


def _is_valid_transition(from_state: RunState, to_state: RunState) -> bool:
    allowed: dict[RunState, set[RunState]] = {
        RunState.SCHEDULED: {RunState.PENDING, RunState.CANCELLED},
        RunState.PENDING: {RunState.RUNNING, RunState.CANCELLED},
        RunState.RUNNING: {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED},
        RunState.COMPLETED: set(),
        RunState.FAILED: set(),
        RunState.CANCELLED: set(),
    }
    return to_state in allowed[from_state]
