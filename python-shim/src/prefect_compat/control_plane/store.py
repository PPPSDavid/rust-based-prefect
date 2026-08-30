from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ..persistence import DEFAULT_WORK_POOL_ID
from .types import (
    FlowRunRecord,
    RunState,
    TaskRunRecord,
)


class StoreMixin:
    def _persist_record(self, record: dict[str, Any]) -> None:
        if self._history_path is None:
            return
        with self._history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record))
            f.write("\n")

    def _load_from_history(self) -> None:
        if self._history_path is None or not self._history_path.exists():
            return
        with self._lock:
            for line in self._history_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                self._apply_record(rec)

    def _apply_record(self, rec: dict[str, Any]) -> None:  # noqa: C901
        record_type = rec.get("record_type")
        if record_type == "flow_create":
            run_id = UUID(rec["run_id"])
            flow = FlowRunRecord(
                run_id=run_id,
                name=rec["name"],
                state=RunState(rec["state"]),
                version=int(rec["version"]),
                parent_flow_run_id=UUID(rec["parent_flow_run_id"])
                if rec.get("parent_flow_run_id")
                else None,
                parent_task_run_id=UUID(rec["parent_task_run_id"])
                if rec.get("parent_task_run_id")
                else None,
                root_flow_run_id=UUID(rec["root_flow_run_id"])
                if rec.get("root_flow_run_id")
                else run_id,
                execution_mode=rec.get("execution_mode"),
                depth=int(rec.get("depth", 0)),
            )
            self._flows[run_id] = flow
            self._latest_flow_run_id = run_id
            if self._replay_to_sqlite:
                self._insert_flow_row(flow)
            self._rust_register_flow(flow)
        elif record_type == "task_create":
            task_id = UUID(rec["task_run_id"])
            task = TaskRunRecord(
                task_run_id=task_id,
                flow_run_id=UUID(rec["flow_run_id"]),
                task_name=rec["task_name"],
                planned_node_id=rec.get("planned_node_id"),
                state=RunState(rec["state"]),
                version=int(rec["version"]),
                kind=rec.get("kind", "task"),
                child_flow_run_id=UUID(rec["child_flow_run_id"])
                if rec.get("child_flow_run_id")
                else None,
                child_deployment_run_id=UUID(rec["child_deployment_run_id"])
                if rec.get("child_deployment_run_id")
                else None,
            )
            self._tasks[task_id] = task
            if self._replay_to_sqlite:
                self._insert_task_row(task)
            self._rust_register_task(task)
        elif record_type == "flow_transition":
            run_id = UUID(rec["run_id"])
            if run_id in self._flows:
                flow = self._flows[run_id]
                from_state = flow.state.value
                if self._rust_fsm_active() and rec.get("transition_token"):
                    out = self._rust_fsm_call(
                        "set_flow_state",
                        {
                            "run_id": str(run_id),
                            "to_state": rec["to_state"],
                            "transition_token": str(rec["transition_token"]),
                            "transition_kind": rec.get("kind", "replay"),
                            "expected_version": int(rec["version"]) - 1,
                        },
                    )
                    if not out.get("ok", True):
                        self._raise_from_rust_fsm_error(out.get("error", {}))
                    flow.state = RunState(str(out["current_state"]))
                    flow.version = int(out["version"])
                else:
                    flow.state = RunState(rec["to_state"])
                    flow.version = int(rec["version"])
                    if self._rust_fsm_active():
                        self._rust_fsm_call(
                            "apply_flow_checkpoint",
                            {
                                "run_id": str(run_id),
                                "state": rec["to_state"],
                                "version": int(rec["version"]),
                            },
                        )
                if self._replay_to_sqlite:
                    self._update_flow_row(flow)
                    event = {
                        "event_id": rec.get("event_id", str(uuid4())),
                        "run_id": str(run_id),
                        "from_state": from_state,
                        "to_state": flow.state.value,
                        "kind": rec.get("kind", "replay"),
                    }
                    self._insert_event_row(event)
                    self._insert_log_row(
                        {
                            "flow_run_id": str(run_id),
                            "task_run_id": None,
                            "level": "INFO",
                            "message": f"Flow state transition {from_state} -> {flow.state.value}",
                        }
                    )
            token = rec.get("transition_token")
            if token:
                self._tokens.add(UUID(str(token)))
        elif record_type == "task_subflow_linkage":
            task_id = UUID(rec["task_run_id"])
            child_flow_run_id = (
                UUID(str(rec["child_flow_run_id"]))
                if rec.get("child_flow_run_id")
                else None
            )
            child_deployment_run_id = (
                UUID(str(rec["child_deployment_run_id"]))
                if rec.get("child_deployment_run_id")
                else None
            )
            if task_id in self._tasks:
                task = self._tasks[task_id]
                if child_flow_run_id is not None:
                    task.child_flow_run_id = child_flow_run_id
                if child_deployment_run_id is not None:
                    task.child_deployment_run_id = child_deployment_run_id
            updates: list[str] = []
            params: list[str] = []
            if child_flow_run_id is not None:
                updates.append("child_flow_run_id = ?")
                params.append(str(child_flow_run_id))
            if child_deployment_run_id is not None:
                updates.append("child_deployment_run_id = ?")
                params.append(str(child_deployment_run_id))
            if updates:
                params.append(str(task_id))
                self._sqlite_conn.execute(
                    f"UPDATE task_runs SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
        elif record_type == "task_event":
            task_id = UUID(rec["task_run_id"])
            if task_id in self._tasks:
                task = self._tasks[task_id]
                if self._rust_fsm_active() and rec.get("transition_token"):
                    out = self._rust_fsm_call(
                        "set_task_state",
                        {
                            "task_run_id": str(task_id),
                            "to_state": rec["state"],
                            "transition_token": str(rec["transition_token"]),
                            "transition_kind": str(rec.get("event_type", "task_event")),
                            "expected_version": int(rec["version"]) - 1,
                        },
                    )
                    if not out.get("ok", True):
                        self._raise_from_rust_fsm_error(out.get("error", {}))
                    task.state = RunState(str(out["current_state"]))
                    task.version = int(out["version"])
                else:
                    task.state = RunState(rec["state"])
                    task.version = int(rec["version"])
                    if self._rust_fsm_active():
                        self._rust_fsm_call(
                            "apply_task_checkpoint",
                            {
                                "task_run_id": str(task_id),
                                "state": rec["state"],
                                "version": int(rec["version"]),
                            },
                        )
                if self._replay_to_sqlite:
                    self._update_task_row(task)
                    event = {
                        "event_id": rec.get("event_id", str(uuid4())),
                        "run_id": rec.get("flow_run_id", str(task.flow_run_id)),
                        "task_run_id": str(task_id),
                        "event_type": rec.get("event_type"),
                        "data": rec.get("data", {}),
                    }
                    self._insert_event_row(event)
                    log_level = (
                        "ERROR" if rec.get("event_type") == "task_failed" else "INFO"
                    )
                    self._insert_log_row(
                        {
                            "flow_run_id": str(task.flow_run_id),
                            "task_run_id": str(task_id),
                            "level": log_level,
                            "message": f"{task.task_name}: {rec.get('event_type', 'task_event')}",
                        }
                    )
                    if rec.get("event_type") == "task_completed":
                        self._insert_artifact_row(
                            {
                                "task_run_id": str(task_id),
                                "flow_run_id": str(task.flow_run_id),
                                "artifact_type": "result",
                                "key": f"{task.task_name}-result",
                                "summary": json.dumps(rec.get("data", {})),
                            }
                        )
        elif record_type == "flow_lifecycle":
            flow_key = str(rec.get("flow_run_id", ""))
            if flow_key:
                action = rec.get("lifecycle_action")
                mode = rec.get("interrupt_mode")
                if action is None and mode is None:
                    self._lifecycle_by_flow.pop(flow_key, None)
                else:
                    self._lifecycle_by_flow[flow_key] = {
                        "lifecycle_action": action,
                        "interrupt_mode": mode,
                        "pause_drain_pending": bool(
                            rec.get("pause_drain_pending", False)
                        ),
                        "lifecycle_summary": rec.get("lifecycle_summary"),
                    }

        if record_type in {"flow_transition", "task_event"}:
            # Rebuild in-memory event stream from persisted history.
            self._events.append(rec)

    def _read_db_empty_unlocked(self) -> bool:
        row = self._sqlite_conn.execute(
            "SELECT COUNT(1) AS count FROM flow_runs"
        ).fetchone()
        if row is None:
            return True
        return int(row["count"]) == 0

    def _query_rows(self, query: str, params: list[Any]) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._sqlite_conn.execute(query, params)
            return list(cur.fetchall())

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _insert_flow_row(self, record: FlowRunRecord) -> None:
        now = self._now()
        self._sqlite_conn.execute(
            "INSERT OR IGNORE INTO flow_runs(id,name,state,version,created_at,updated_at,"
            "parent_flow_run_id,parent_task_run_id,root_flow_run_id,execution_mode,depth) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [
                str(record.run_id),
                record.name,
                record.state.value,
                record.version,
                now,
                now,
                str(record.parent_flow_run_id) if record.parent_flow_run_id else None,
                str(record.parent_task_run_id) if record.parent_task_run_id else None,
                str(record.root_flow_run_id) if record.root_flow_run_id else None,
                record.execution_mode,
                record.depth,
            ],
        )
        if (
            record.resume_from_flow_run_id is not None
            or record.resume_lineage_id is not None
            or record.parameters_fingerprint is not None
        ):
            self._ensure_resume_schema()
            self._sqlite_conn.execute(
                "UPDATE flow_runs SET resume_from_flow_run_id = ?, "
                "resume_lineage_id = ?, parameters_fingerprint = ? "
                "WHERE id = ?",
                [
                    str(record.resume_from_flow_run_id)
                    if record.resume_from_flow_run_id
                    else None,
                    str(record.resume_lineage_id) if record.resume_lineage_id else None,
                    record.parameters_fingerprint,
                    str(record.run_id),
                ],
            )

    def _update_flow_row(self, record: FlowRunRecord) -> None:
        self._sqlite_conn.execute(
            "UPDATE flow_runs SET state = ?, version = ?, updated_at = ? WHERE id = ?",
            [record.state.value, record.version, self._now(), str(record.run_id)],
        )

    def _insert_task_row(self, task: TaskRunRecord) -> None:
        now = self._now()
        self._sqlite_conn.execute(
            "INSERT OR IGNORE INTO task_runs(id,flow_run_id,task_name,planned_node_id,state,version,created_at,updated_at,"
            "kind,child_flow_run_id,child_deployment_run_id,gate_open_at,tags,contribute_to_flow_state,task_run_attempt) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                str(task.task_run_id),
                str(task.flow_run_id),
                task.task_name,
                task.planned_node_id,
                task.state.value,
                task.version,
                now,
                now,
                task.kind,
                str(task.child_flow_run_id) if task.child_flow_run_id else None,
                str(task.child_deployment_run_id)
                if task.child_deployment_run_id
                else None,
                task.gate_open_at,
                json.dumps(list(task.tags)) if task.tags else None,
                1 if task.contribute_to_flow_state else 0,
                task.task_run_attempt,
            ],
        )

    def _update_task_row(self, task: TaskRunRecord) -> None:
        self._sqlite_conn.execute(
            "UPDATE task_runs SET state = ?, version = ?, updated_at = ? WHERE id = ?",
            [task.state.value, task.version, self._now(), str(task.task_run_id)],
        )

    def _insert_event_row(self, event: dict[str, Any]) -> None:
        self._sqlite_conn.execute(
            "INSERT OR IGNORE INTO events(event_id,run_id,task_run_id,from_state,to_state,event_type,kind,data,timestamp) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            [
                event.get("event_id", str(uuid4())),
                event.get("run_id"),
                event.get("task_run_id"),
                event.get("from_state"),
                event.get("to_state"),
                event.get("event_type"),
                event.get("kind"),
                json.dumps(event.get("data", {})),
                self._now(),
            ],
        )

    def append_log(
        self,
        *,
        flow_run_id: UUID | str,
        message: str,
        level: str = "INFO",
        task_run_id: UUID | str | None = None,
    ) -> None:
        """Persist a user log row.

        Serializes the store insert under ``_lock`` (safe for thread-pool
        loggers) but does not perform FSM transitions.
        """
        self._insert_log_row(
            {
                "flow_run_id": str(flow_run_id),
                "task_run_id": str(task_run_id) if task_run_id is not None else None,
                "level": str(level or "INFO").upper(),
                "message": str(message),
            }
        )

    def _insert_log_row(self, log: dict[str, Any]) -> None:
        # sqlite3 connections are not safely concurrent without a lock even in WAL mode.
        with self._lock:
            self._sqlite_conn.execute(
                "INSERT INTO logs(id,flow_run_id,task_run_id,level,message,timestamp) VALUES(?,?,?,?,?,?)",
                [
                    str(uuid4()),
                    log["flow_run_id"],
                    log.get("task_run_id"),
                    log["level"],
                    log["message"],
                    self._now(),
                ],
            )

    def _insert_artifact_row(self, artifact: dict[str, Any]) -> None:
        self._sqlite_conn.execute(
            "INSERT INTO artifacts(id,flow_run_id,task_run_id,artifact_type,key,summary,created_at) VALUES(?,?,?,?,?,?,?)",
            [
                str(uuid4()),
                artifact["flow_run_id"],
                artifact.get("task_run_id"),
                artifact["artifact_type"],
                artifact["key"],
                artifact.get("summary"),
                self._now(),
            ],
        )

    def _flow_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        return {
            "id": row["id"],
            "name": row["name"],
            "state": row["state"],
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "parent_flow_run_id": row["parent_flow_run_id"]
            if "parent_flow_run_id" in keys
            else None,
            "parent_task_run_id": row["parent_task_run_id"]
            if "parent_task_run_id" in keys
            else None,
            "root_flow_run_id": row["root_flow_run_id"]
            if "root_flow_run_id" in keys
            else None,
            "execution_mode": row["execution_mode"]
            if "execution_mode" in keys
            else None,
            "depth": row["depth"] if "depth" in keys else 0,
        }

    def _task_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        return {
            "id": row["id"],
            "flow_run_id": row["flow_run_id"],
            "task_name": row["task_name"],
            "planned_node_id": row["planned_node_id"],
            "state": row["state"],
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "kind": row["kind"] if "kind" in keys else "task",
            "child_flow_run_id": row["child_flow_run_id"]
            if "child_flow_run_id" in keys
            else None,
            "child_deployment_run_id": row["child_deployment_run_id"]
            if "child_deployment_run_id" in keys
            else None,
            "task_run_attempt": int(row["task_run_attempt"])
            if "task_run_attempt" in keys and row["task_run_attempt"] is not None
            else 1,
        }

    def _log_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "flow_run_id": row["flow_run_id"],
            "task_run_id": row["task_run_id"],
            "level": row["level"],
            "message": row["message"],
            "timestamp": row["timestamp"],
        }

    def _event_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "run_id": row["run_id"],
            "task_run_id": row["task_run_id"],
            "from_state": row["from_state"],
            "to_state": row["to_state"],
            "event_type": row["event_type"],
            "kind": row["kind"],
            "data": json.loads(row["data"] or "{}"),
            "timestamp": row["timestamp"],
        }

    def _artifact_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "flow_run_id": row["flow_run_id"],
            "task_run_id": row["task_run_id"],
            "artifact_type": row["artifact_type"],
            "key": row["key"],
            "summary": row["summary"],
            "created_at": row["created_at"],
        }

    def _deployment_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()

        def col(name: str, default: Any = None) -> Any:
            return row[name] if name in keys else default

        return {
            "id": row["id"],
            "name": row["name"],
            "flow_name": row["flow_name"],
            "entrypoint": row["entrypoint"],
            "path": row["path"],
            "default_parameters": json.loads(row["default_parameters"] or "{}"),
            "paused": bool(row["paused"]),
            "concurrency_limit": col("concurrency_limit"),
            "collision_strategy": col("collision_strategy") or "ENQUEUE",
            "schedule_interval_seconds": col("schedule_interval_seconds"),
            "schedule_cron": col("schedule_cron"),
            "schedule_rrule": col("schedule_rrule"),
            "schedule_next_run_at": col("schedule_next_run_at"),
            "schedule_enabled": bool(col("schedule_enabled", 0)),
            "work_pool_id": col("work_pool_id") or DEFAULT_WORK_POOL_ID,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _work_pool_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "paused": bool(row["paused"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _worker_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        return {
            "name": row["name"],
            "status": row["status"],
            "last_heartbeat": row["last_heartbeat"],
            "updated_at": row["updated_at"],
            "work_pool_id": row["work_pool_id"] if "work_pool_id" in keys else None,
        }

    def _deployment_run_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        return {
            "id": row["id"],
            "deployment_id": row["deployment_id"],
            "status": row["status"],
            "requested_parameters": json.loads(row["requested_parameters"] or "{}"),
            "resolved_parameters": json.loads(row["resolved_parameters"] or "{}"),
            "idempotency_key": row["idempotency_key"],
            "worker_name": row["worker_name"],
            "lease_until": row["lease_until"],
            "flow_run_id": row["flow_run_id"],
            "error": row["error"],
            "parent_flow_run_id": row["parent_flow_run_id"]
            if "parent_flow_run_id" in keys
            else None,
            "parent_task_run_id": row["parent_task_run_id"]
            if "parent_task_run_id" in keys
            else None,
            "parent_deployment_run_id": row["parent_deployment_run_id"]
            if "parent_deployment_run_id" in keys
            else None,
            "resume_from_flow_run_id": row["resume_from_flow_run_id"]
            if "resume_from_flow_run_id" in keys
            else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }
