from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
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


@dataclass
class PageResult:
    items: list[dict[str, Any]]
    next_cursor: str | None


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
        sqlite_path = Path(history_path).with_suffix(".db") if history_path else Path("data") / "ironflow_ui.db"
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._sqlite_path = sqlite_path
        self._init_read_db()
        if self._history_path is not None:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._load_from_history()

    def create_flow_run(self, name: str) -> FlowRunRecord:
        record = FlowRunRecord(run_id=uuid4(), name=name, state=RunState.SCHEDULED, version=0)
        with self._lock:
            self._flows[record.run_id] = record
            self._latest_flow_run_id = record.run_id
            self._insert_flow_row(record)
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
            self._insert_task_row(task)
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
            event = self._events[-1]
            self._insert_event_row(event)

            record.state = to_state
            record.version += 1
            self._update_flow_row(record)
            self._insert_log_row(
                {
                    "flow_run_id": str(run_id),
                    "task_run_id": None,
                    "level": "INFO",
                    "message": f"Flow state transition {event['from_state']} -> {event['to_state']}",
                }
            )
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

    def list_flow_runs(self, state: str | None = None, limit: int = 50, cursor: str | None = None) -> PageResult:
        query = "SELECT seq,id,name,state,version,created_at,updated_at FROM flow_runs"
        conditions: list[str] = []
        params: list[Any] = []
        if state:
            conditions.append("state = ?")
            params.append(state)
        if cursor:
            conditions.append("seq < ?")
            params.append(int(cursor))
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._flow_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def get_flow_run_detail(self, flow_run_id: UUID) -> dict[str, Any] | None:
        rows = self._query_rows(
            "SELECT seq,id,name,state,version,created_at,updated_at FROM flow_runs WHERE id = ? LIMIT 1",
            [str(flow_run_id)],
        )
        if not rows:
            return None
        return self._flow_row_to_dict(rows[0])

    def list_task_runs(
        self, flow_run_id: UUID, limit: int = 200, cursor: str | None = None
    ) -> PageResult:
        query = (
            "SELECT seq,id,flow_run_id,task_name,state,version,created_at,updated_at "
            "FROM task_runs WHERE flow_run_id = ?"
        )
        params: list[Any] = [str(flow_run_id)]
        if cursor:
            query += " AND seq < ?"
            params.append(int(cursor))
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._task_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def list_logs(
        self,
        flow_run_id: UUID,
        task_run_id: UUID | None = None,
        level: str | None = None,
        limit: int = 500,
        cursor: str | None = None,
    ) -> PageResult:
        query = (
            "SELECT seq,id,flow_run_id,task_run_id,level,message,timestamp "
            "FROM logs WHERE flow_run_id = ?"
        )
        params: list[Any] = [str(flow_run_id)]
        if task_run_id:
            query += " AND task_run_id = ?"
            params.append(str(task_run_id))
        if level:
            query += " AND level = ?"
            params.append(level.upper())
        if cursor:
            query += " AND seq < ?"
            params.append(int(cursor))
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._log_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def list_flows(self, limit: int = 200, cursor: str | None = None) -> PageResult:
        query = (
            "SELECT seq,name,MAX(updated_at) AS updated_at,COUNT(*) AS run_count "
            "FROM flow_runs"
        )
        params: list[Any] = []
        if cursor:
            query += " WHERE seq < ?"
            params.append(int(cursor))
        query += " GROUP BY name ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [
            {
                "name": row["name"],
                "run_count": row["run_count"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def list_tasks(self, flow_name: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = (
            "SELECT tr.task_name AS task_name, COUNT(*) AS run_count, MAX(tr.updated_at) AS updated_at "
            "FROM task_runs tr "
            "JOIN flow_runs fr ON fr.id = tr.flow_run_id"
        )
        params: list[Any] = []
        if flow_name:
            query += " WHERE fr.name = ?"
            params.append(flow_name)
        query += " GROUP BY tr.task_name ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        return [
            {"task_name": row["task_name"], "run_count": row["run_count"], "updated_at": row["updated_at"]}
            for row in rows
        ]

    def list_events(self, flow_run_id: UUID, limit: int = 500, cursor: str | None = None) -> PageResult:
        query = (
            "SELECT seq,event_id,run_id,task_run_id,from_state,to_state,event_type,kind,data,timestamp "
            "FROM events WHERE run_id = ?"
        )
        params: list[Any] = [str(flow_run_id)]
        if cursor:
            query += " AND seq < ?"
            params.append(int(cursor))
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._event_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def list_artifacts_for_flow(self, flow_run_id: UUID, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._query_rows(
            "SELECT id,flow_run_id,task_run_id,artifact_type,key,summary,created_at "
            "FROM artifacts WHERE flow_run_id = ? ORDER BY created_at DESC LIMIT ?",
            [str(flow_run_id), limit],
        )
        return [self._artifact_row_to_dict(r) for r in rows]

    def list_artifacts_for_task(self, task_run_id: UUID, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._query_rows(
            "SELECT id,flow_run_id,task_run_id,artifact_type,key,summary,created_at "
            "FROM artifacts WHERE task_run_id = ? ORDER BY created_at DESC LIMIT ?",
            [str(task_run_id), limit],
        )
        return [self._artifact_row_to_dict(r) for r in rows]

    def get_artifact(self, artifact_id: UUID) -> dict[str, Any] | None:
        rows = self._query_rows(
            "SELECT id,flow_run_id,task_run_id,artifact_type,key,summary,created_at "
            "FROM artifacts WHERE id = ? LIMIT 1",
            [str(artifact_id)],
        )
        return self._artifact_row_to_dict(rows[0]) if rows else None

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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_read_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS flow_runs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_runs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL,
                    flow_run_id TEXT NOT NULL,
                    task_name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS logs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL,
                    flow_run_id TEXT NOT NULL,
                    task_run_id TEXT,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    run_id TEXT NOT NULL,
                    task_run_id TEXT,
                    from_state TEXT,
                    to_state TEXT,
                    event_type TEXT,
                    kind TEXT,
                    data TEXT,
                    timestamp TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL,
                    flow_run_id TEXT NOT NULL,
                    task_run_id TEXT,
                    artifact_type TEXT NOT NULL,
                    key TEXT NOT NULL,
                    summary TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_flow_runs_state_created
                    ON flow_runs(state, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_task_runs_flow_start
                    ON task_runs(flow_run_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_logs_flow_ts
                    ON logs(flow_run_id, timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_logs_task_ts
                    ON logs(task_run_id, timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_events_run_ts
                    ON events(run_id, timestamp DESC);
                """
            )

    def _query_rows(self, query: str, params: list[Any]) -> list[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(query, params)
            return cur.fetchall()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _insert_flow_row(self, record: FlowRunRecord) -> None:
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO flow_runs(id,name,state,version,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                [str(record.run_id), record.name, record.state.value, record.version, now, now],
            )

    def _update_flow_row(self, record: FlowRunRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE flow_runs SET state = ?, version = ?, updated_at = ? WHERE id = ?",
                [record.state.value, record.version, self._now(), str(record.run_id)],
            )

    def _insert_task_row(self, task: TaskRunRecord) -> None:
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO task_runs(id,flow_run_id,task_name,state,version,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                [
                    str(task.task_run_id),
                    str(task.flow_run_id),
                    task.task_name,
                    task.state.value,
                    task.version,
                    now,
                    now,
                ],
            )

    def _update_task_row(self, task: TaskRunRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET state = ?, version = ?, updated_at = ? WHERE id = ?",
                [task.state.value, task.version, self._now(), str(task.task_run_id)],
            )

    def _insert_event_row(self, event: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
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

    def _insert_log_row(self, log: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
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
        with self._connect() as conn:
            conn.execute(
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
        return {
            "id": row["id"],
            "name": row["name"],
            "state": row["state"],
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _task_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "flow_run_id": row["flow_run_id"],
            "task_name": row["task_name"],
            "state": row["state"],
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
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
