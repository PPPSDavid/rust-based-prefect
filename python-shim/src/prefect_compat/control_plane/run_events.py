from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from .types import RunState


class RunEventsMixin:
    def record_task_event(
        self, task_run_id: UUID, event_type: str, data: dict[str, Any] | None = None
    ) -> None:
        event_to_state: dict[str, RunState] = {
            "task_pending": RunState.PENDING,
            "task_running": RunState.RUNNING,
            "task_completed": RunState.COMPLETED,
            "task_failed": RunState.FAILED,
            "task_cancelled": RunState.CANCELLED,
        }
        flow_run_id_for_settle: UUID | None = None
        with self._lock:
            task = self._tasks[task_run_id]
            transition_token: UUID | None = None
            from_state: str | None = None

            fenced_late_event = task.state == RunState.CANCELLED and event_type in {
                "task_completed",
                "task_failed",
                "task_running",
                "task_pending",
            }
            if fenced_late_event:
                flow_run_id_for_settle = task.flow_run_id
                from_state = None
            elif self._rust_fsm_active() and event_type in event_to_state:
                from_state = task.state.value
                to_state = event_to_state[event_type]
                transition_token = uuid4()
                req = {
                    "task_run_id": str(task_run_id),
                    "to_state": to_state.value,
                    "expected_version": int(task.version),
                    "transition_token": str(transition_token),
                    "transition_kind": event_type,
                }
                use_native_persist = self._rust_native_persistence
                op = (
                    "set_task_state_persist" if use_native_persist else "set_task_state"
                )
                payload: dict[str, Any] = (
                    self._persist_payload(req, event_type=event_type, data=data or {})
                    if use_native_persist
                    else req
                )
                out = self._rust_fsm_call(op, payload)
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if use_native_persist and self._is_unknown_op_error(
                        err, "set_task_state_persist"
                    ):
                        self._rust_native_persistence = False
                        out = self._rust_fsm_call("set_task_state", req)
                    if not out.get("ok", True):
                        self._raise_from_rust_fsm_error(out.get("error", {}))
                status = str(out["status"])
                if status == "duplicate":
                    task.state = RunState(str(out["current_state"]))
                    task.version = int(out["version"])
                    flow_run_id_for_settle = task.flow_run_id
                else:
                    task.state = RunState(str(out["current_state"]))
                    task.version = int(out["version"])
            else:
                # Fence: terminal CANCELLED must not be overwritten by late COMPLETED/FAILED.
                if task.state == RunState.CANCELLED and event_type in {
                    "task_completed",
                    "task_failed",
                    "task_running",
                    "task_pending",
                }:
                    flow_run_id_for_settle = task.flow_run_id
                    from_state = None
                elif event_type == "task_pending":
                    task.state = RunState.PENDING
                    task.version += 1
                    from_state = None
                elif event_type == "task_running":
                    task.state = RunState.RUNNING
                    task.version += 1
                    from_state = None
                elif event_type == "task_completed":
                    task.state = RunState.COMPLETED
                    task.version += 1
                    from_state = None
                elif event_type == "task_failed":
                    task.state = RunState.FAILED
                    task.version += 1
                    from_state = None
                elif event_type == "task_cancelled":
                    task.state = RunState.CANCELLED
                    task.version += 1
                    from_state = None
                else:
                    from_state = None

            if flow_run_id_for_settle is None:
                # Normal (non-duplicate) path: persist event rows.
                ev: dict[str, Any] = {
                    "event_id": str(uuid4()),
                    "run_id": str(task.flow_run_id),
                    "task_run_id": str(task_run_id),
                    "event_type": event_type,
                    "data": data or {},
                }
                if from_state is not None:
                    ev["from_state"] = from_state
                    ev["to_state"] = task.state.value
                self._events.append(ev)
                if (not self._rust_fsm_active()) or (not self._rust_native_persistence):
                    self._insert_event_row(self._events[-1])
                    self._update_task_row(task)
                    log_level = "ERROR" if event_type == "task_failed" else "INFO"
                    self._insert_log_row(
                        {
                            "flow_run_id": str(task.flow_run_id),
                            "task_run_id": str(task_run_id),
                            "level": log_level,
                            "message": f"{task.task_name}: {event_type}",
                        }
                    )
                    if event_type == "task_completed":
                        self._insert_artifact_row(
                            {
                                "task_run_id": str(task_run_id),
                                "flow_run_id": str(task.flow_run_id),
                                "artifact_type": "result",
                                "key": f"{task.task_name}-result",
                                "summary": json.dumps(data or {}),
                            }
                        )
                rec: dict[str, Any] = {
                    "record_type": "task_event",
                    "task_run_id": str(task_run_id),
                    "flow_run_id": str(task.flow_run_id),
                    "event_type": event_type,
                    "state": task.state.value,
                    "version": task.version,
                    "data": data or {},
                }
                if transition_token is not None:
                    rec["transition_token"] = str(transition_token)
                self._persist_record(rec)
                flow_run_id_for_settle = task.flow_run_id

        if flow_run_id_for_settle is not None and event_type in {
            "task_completed",
            "task_failed",
            "task_cancelled",
        }:
            self._maybe_settle_drain_pause(flow_run_id_for_settle)

    def record_task_events_batch(
        self,
        task_run_id: UUID,
        events: list[tuple[str, dict[str, Any] | None]],
    ) -> None:
        if not events:
            return
        if len(events) < self._TASK_BATCH_MIN_SIZE:
            for evt, payload in events:
                self.record_task_event(task_run_id, evt, payload)
            return
        event_to_state: dict[str, RunState] = {
            "task_pending": RunState.PENDING,
            "task_running": RunState.RUNNING,
            "task_completed": RunState.COMPLETED,
            "task_failed": RunState.FAILED,
            "task_cancelled": RunState.CANCELLED,
        }
        with self._lock:
            task = self._tasks[task_run_id]
            if (
                self._rust_fsm_active()
                and self._rust_native_persistence
                and all(evt in event_to_state for evt, _ in events)
            ):
                expected_version = int(task.version)
                prev_state = task.state
                req_items: list[dict[str, Any]] = []
                tokens: list[UUID] = []
                states: list[RunState] = []
                payload_data: list[dict[str, Any]] = []
                for evt, payload in events:
                    token = uuid4()
                    to_state = event_to_state[evt]
                    req_items.append(
                        {
                            "event_type": evt,
                            "data": payload or {},
                            "request": {
                                "task_run_id": str(task_run_id),
                                "to_state": to_state.value,
                                "expected_version": expected_version,
                                "transition_token": str(token),
                                "transition_kind": evt,
                            },
                        }
                    )
                    tokens.append(token)
                    states.append(to_state)
                    payload_data.append(payload or {})
                    expected_version += 1
                out = self._rust_fsm_call(
                    "set_task_states_persist_batch",
                    {
                        **(
                            {}
                            if self._rust_db_bound
                            else {"db_path": str(self._sqlite_path)}
                        ),
                        "items": req_items,
                    },
                )
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if self._is_unknown_op_error(err, "set_task_states_persist_batch"):
                        self._rust_native_persistence = False
                        for evt, payload in events:
                            self.record_task_event(task_run_id, evt, payload)
                        return
                    self._raise_from_rust_fsm_error(err)
                results = out.get("results", [])
                for i, result in enumerate(results):
                    status = str(result.get("status", ""))
                    task.state = RunState(
                        str(result.get("current_state", task.state.value))
                    )
                    task.version = int(result.get("version", task.version))
                    if status == "duplicate":
                        prev_state = task.state
                        continue
                    ev = {
                        "event_id": str(uuid4()),
                        "run_id": str(task.flow_run_id),
                        "task_run_id": str(task_run_id),
                        "event_type": events[i][0],
                        "from_state": prev_state.value,
                        "to_state": states[i].value,
                        "data": payload_data[i],
                    }
                    self._events.append(ev)
                    self._persist_record(
                        {
                            "record_type": "task_event",
                            "task_run_id": str(task_run_id),
                            "flow_run_id": str(task.flow_run_id),
                            "event_type": events[i][0],
                            "state": task.state.value,
                            "version": task.version,
                            "data": payload_data[i],
                            "transition_token": str(tokens[i]),
                        }
                    )
                    prev_state = task.state
                return
            for evt, payload in events:
                self.record_task_event(task_run_id, evt, payload)

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

    def set_flow_result(self, run_id: UUID, result: Any) -> None:
        with self._lock:
            self._flow_results[run_id] = result

    def get_flow_result(self, run_id: UUID) -> Any:
        with self._lock:
            return self._flow_results.get(run_id)
