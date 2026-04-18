from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

try:
    from .rust_bridge import RustFsmBridge, RustQueryBridge
except Exception:  # pragma: no cover - best-effort optional accelerator
    RustQueryBridge = None  # type: ignore[assignment]
    RustFsmBridge = None  # type: ignore[assignment]


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
    planned_node_id: str | None
    state: RunState
    version: int


@dataclass
class PageResult:
    items: list[dict[str, Any]]
    next_cursor: str | None


@dataclass
class DeploymentRecord:
    deployment_id: UUID
    name: str
    flow_name: str
    entrypoint: str | None
    path: str | None
    default_parameters: dict[str, Any]
    paused: bool


class InMemoryControlPlane:
    _FLOW_BATCH_MIN_SIZE = 3
    _TASK_BATCH_MIN_SIZE = 3

    """
    Python MVP control plane with the same transition semantics as the Rust engine.
    This is used by the shim and can be swapped for an HTTP Rust facade.
    """

    def __init__(self, history_path: str | None = None) -> None:
        self._flows: dict[UUID, FlowRunRecord] = {}
        self._tasks: dict[UUID, TaskRunRecord] = {}
        self._events: list[dict[str, Any]] = []
        self._tokens: set[UUID] = set()
        self._lock = RLock()
        self._latest_flow_run_id: UUID | None = None
        self._history_path = Path(history_path) if history_path else None
        sqlite_path = Path(history_path).with_suffix(".db") if history_path else Path("data") / "ironflow_ui.db"
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._sqlite_path = sqlite_path
        self._manifest_by_task: dict[UUID, dict[str, list[str]]] = {}
        self._sqlite_conn = self._open_sqlite_connection(sqlite_path)
        self._init_sqlite_schema(self._sqlite_conn)
        self._ensure_schema_upgrades(self._sqlite_conn)
        self._replay_to_sqlite = self._read_db_empty_unlocked()
        self._rust_bridge = None
        self._rust_fsm_bridge = None
        self._rust_fsm_handle = 0
        self._rust_native_persistence = True
        self._rust_db_bound = False
        if RustQueryBridge is not None:
            try:
                self._rust_bridge = RustQueryBridge()
            except Exception:
                self._rust_bridge = None
        use_rust_fsm = os.getenv("IRONFLOW_USE_RUST_FSM", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        if use_rust_fsm and RustFsmBridge is not None:
            try:
                self._rust_fsm_bridge = RustFsmBridge()
                self._rust_fsm_handle = self._rust_fsm_bridge.engine_new()
                try:
                    bind_out = self._rust_fsm_call("bind_db", {"db_path": str(self._sqlite_path)})
                    self._rust_db_bound = bool(bind_out.get("ok", False))
                except Exception:
                    self._rust_db_bound = False
            except Exception:
                self._rust_fsm_bridge = None
                self._rust_fsm_handle = 0
        if self._history_path is not None:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._load_from_history()
        self._rebuild_manifest_cache_from_db()
        self._warned_deployment_fallback = False

    def _open_sqlite_connection(self, sqlite_path: Path) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(sqlite_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.isolation_level = None  # autocommit per statement
            return conn
        except sqlite3.DatabaseError as exc:
            if "malformed" not in str(exc).lower():
                raise
            # Recover local dev/test DBs that got corrupted by abrupt interruption.
            try:
                sqlite_path.unlink(missing_ok=True)
            except Exception:
                pass
            conn = sqlite3.connect(str(sqlite_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.isolation_level = None
            return conn

    def __del__(self) -> None:
        bridge = getattr(self, "_rust_fsm_bridge", None)
        handle = getattr(self, "_rust_fsm_handle", 0)
        if bridge is not None and handle:
            try:
                bridge.engine_free(handle)
            except Exception:
                pass

    def _rust_fsm_active(self) -> bool:
        return bool(getattr(self, "_rust_fsm_handle", 0))

    def _rust_fsm_call(self, op: str, body: dict[str, Any]) -> dict[str, Any]:
        bridge = self._rust_fsm_bridge
        handle = self._rust_fsm_handle
        if not bridge or not handle:
            raise RuntimeError("Rust FSM bridge is not initialized")
        return bridge.control(handle, op, body)

    def _persist_payload(self, request: dict[str, Any], **extras: Any) -> dict[str, Any]:
        payload = dict(extras)
        if not self._rust_db_bound:
            payload["db_path"] = str(self._sqlite_path)
        payload["request"] = request
        return payload

    @staticmethod
    def _raise_from_rust_fsm_error(err: dict[str, Any]) -> None:
        code = err.get("code")
        if code == "invalid_transition":
            from_s = err.get("from")
            to_s = err.get("to")
            raise ValueError(f"invalid transition {from_s} -> {to_s}")
        if code == "version_conflict":
            raise ValueError(
                f"version conflict expected={err.get('expected')} actual={err.get('actual')}"
            )
        raise ValueError(err.get("message", str(err)))

    @staticmethod
    def _is_unknown_op_error(err: dict[str, Any], op: str) -> bool:
        msg = str(err.get("message", ""))
        return f"unknown control op: {op}" in msg

    def _rust_deployment_dispatch(self, op: str, body: dict[str, Any]) -> dict[str, Any] | None:
        """Invoke Rust deployment ops on the bound SQLite connection. None = use Python fallback."""
        if not self._rust_fsm_active() or not self._rust_db_bound:
            if self._rust_fsm_active() and not self._rust_db_bound and not self._warned_deployment_fallback:
                logging.getLogger(__name__).warning(
                    "IronFlow deployment op %s using Python fallback (Rust FSM active but bind_db failed).",
                    op,
                )
                self._warned_deployment_fallback = True
            return None
        try:
            out = self._rust_fsm_call(op, body)
        except Exception:
            return None
        if not out.get("ok", True):
            err = out.get("error") or {}
            if self._is_unknown_op_error(err, op):
                return None
        return out

    @staticmethod
    def _deployment_from_rust_json(d: dict[str, Any]) -> dict[str, Any]:
        """Normalize Rust JSON deployment to match _deployment_row_to_dict shape."""
        dp = d.get("default_parameters")
        if not isinstance(dp, dict):
            dp = {}
        return {
            "id": d["id"],
            "name": d["name"],
            "flow_name": d["flow_name"],
            "entrypoint": d.get("entrypoint"),
            "path": d.get("path"),
            "default_parameters": dp,
            "paused": bool(d.get("paused")),
            "concurrency_limit": d.get("concurrency_limit"),
            "collision_strategy": d.get("collision_strategy") or "ENQUEUE",
            "schedule_interval_seconds": d.get("schedule_interval_seconds"),
            "schedule_cron": d.get("schedule_cron"),
            "schedule_next_run_at": d.get("schedule_next_run_at"),
            "schedule_enabled": bool(d.get("schedule_enabled")),
            "created_at": d["created_at"],
            "updated_at": d["updated_at"],
        }

    def start_rust_deployment_scheduler(self, interval_ms: int = 1000, stale_after_seconds: int = 120) -> bool:
        bridge = self._rust_fsm_bridge
        handle = self._rust_fsm_handle
        if not bridge or not handle or not self._rust_db_bound:
            return False
        return bool(bridge.deployment_scheduler_start(handle, interval_ms, stale_after_seconds))

    def stop_rust_deployment_scheduler(self) -> None:
        bridge = self._rust_fsm_bridge
        handle = self._rust_fsm_handle
        if bridge and handle:
            bridge.deployment_scheduler_stop(handle)

    def _count_exec_runs(self, deployment_id: str) -> int:
        rows = self._query_rows(
            """
            SELECT COUNT(*) AS c FROM deployment_runs
            WHERE deployment_id = ? AND status IN ('CLAIMED','RUNNING')
            """,
            [deployment_id],
        )
        return int(rows[0]["c"]) if rows else 0

    def _reclaim_expired_claims_python(self) -> int:
        now = self._now()
        cur = self._sqlite_conn.execute(
            """
            UPDATE deployment_runs
            SET status = 'SCHEDULED', worker_name = NULL, lease_until = NULL, updated_at = ?
            WHERE status = 'CLAIMED' AND lease_until IS NOT NULL AND lease_until < ?
            """,
            [now, now],
        )
        return int(cur.rowcount or 0)

    def deployment_maintenance_tick(self, stale_after_seconds: int = 120) -> dict[str, Any]:
        """Reclaim leases, fire due schedules, mark stale workers — prefers a single Rust FFI call."""
        rust = self._rust_deployment_dispatch(
            "deployment_maintenance", {"stale_after_seconds": stale_after_seconds}
        )
        if rust is not None and rust.get("ok"):
            return dict(rust.get("summary") or {})
        with self._lock:
            reclaimed = self._reclaim_expired_claims_python()
            n_tick = self._tick_deployment_schedules_python()
            now = self._now()
            cutoff = (datetime.now(UTC) - timedelta(seconds=max(1, stale_after_seconds))).isoformat()
            cur = self._sqlite_conn.execute(
                "UPDATE workers SET status = 'OFFLINE', updated_at = ? WHERE status = 'ONLINE' AND last_heartbeat < ?",
                [now, cutoff],
            )
            reaped = int(cur.rowcount or 0)
        return {"reclaimed": reclaimed, "triggered": n_tick, "reaped": reaped}

    def _tick_deployment_schedules_python(self) -> int:
        now = self._now()
        due = self._query_rows(
            """
            SELECT id, schedule_interval_seconds FROM deployments
            WHERE schedule_enabled = 1 AND paused = 0
              AND schedule_interval_seconds IS NOT NULL AND schedule_interval_seconds > 0
              AND schedule_next_run_at IS NOT NULL AND schedule_next_run_at <= ?
            """,
            [now],
        )
        fired = 0
        for row in due:
            dep_id = UUID(row["id"])
            interval_sec = int(row["schedule_interval_seconds"])
            try:
                self.trigger_deployment_run(dep_id, parameters={}, idempotency_key=None)
            except ValueError:
                continue
            nxt = (datetime.now(UTC) + timedelta(seconds=interval_sec)).isoformat()
            ts = self._now()
            self._sqlite_conn.execute(
                "UPDATE deployments SET schedule_next_run_at = ?, updated_at = ? WHERE id = ?",
                [nxt, ts, str(dep_id)],
            )
            fired += 1
        return fired

    def worker_heartbeat(self, worker_name: str) -> None:
        rust = self._rust_deployment_dispatch("deployment_worker_heartbeat", {"worker_name": worker_name})
        if rust is not None and rust.get("ok"):
            return
        now = self._now()
        with self._lock:
            self._sqlite_conn.execute(
                """
                INSERT INTO workers(name,last_heartbeat,status,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    last_heartbeat=excluded.last_heartbeat,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                [worker_name, now, "ONLINE", now],
            )

    def _rust_register_flow(self, record: FlowRunRecord) -> None:
        if not self._rust_fsm_active():
            return
        self._rust_fsm_call(
            "register_flow",
            {
                "id": str(record.run_id),
                "name": record.name,
                "state": record.state.value,
                "version": int(record.version),
            },
        )

    def _rust_register_task(self, task: TaskRunRecord) -> None:
        if not self._rust_fsm_active():
            return
        self._rust_fsm_call(
            "register_task",
            {
                "id": str(task.task_run_id),
                "flow_run_id": str(task.flow_run_id),
                "task_key": task.task_name,
                "state": task.state.value,
                "version": int(task.version),
            },
        )

    def create_flow_run(self, name: str) -> FlowRunRecord:
        record = FlowRunRecord(run_id=uuid4(), name=name, state=RunState.SCHEDULED, version=0)
        with self._lock:
            persisted_by_rust = False
            if self._rust_fsm_active() and self._rust_native_persistence:
                out = self._rust_fsm_call(
                    "create_flow_run_persist",
                    {
                        **({} if self._rust_db_bound else {"db_path": str(self._sqlite_path)}),
                        "run": {
                            "id": str(record.run_id),
                            "name": record.name,
                            "state": record.state.value,
                            "version": int(record.version),
                        },
                    },
                )
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if self._is_unknown_op_error(err, "create_flow_run_persist"):
                        self._rust_native_persistence = False
                    else:
                        self._raise_from_rust_fsm_error(err)
                else:
                    persisted_by_rust = True
            self._flows[record.run_id] = record
            self._latest_flow_run_id = record.run_id
            if not persisted_by_rust:
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
            if (not self._rust_native_persistence) and self._rust_fsm_active():
                self._rust_register_flow(record)
        return record

    def create_task_run(
        self, flow_run_id: UUID, task_name: str, planned_node_id: str | None = None
    ) -> TaskRunRecord:
        task = TaskRunRecord(
            task_run_id=uuid4(),
            flow_run_id=flow_run_id,
            task_name=task_name,
            planned_node_id=planned_node_id,
            state=RunState.SCHEDULED,
            version=0,
        )
        with self._lock:
            self._tasks[task.task_run_id] = task
            persisted_by_rust = False
            if self._rust_fsm_active() and self._rust_native_persistence:
                out = self._rust_fsm_call(
                    "create_task_run_persist",
                    {
                        **({} if self._rust_db_bound else {"db_path": str(self._sqlite_path)}),
                        "planned_node_id": task.planned_node_id,
                        "task": {
                            "id": str(task.task_run_id),
                            "flow_run_id": str(task.flow_run_id),
                            "task_key": task.task_name,
                            "state": task.state.value,
                            "version": int(task.version),
                        },
                    },
                )
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if self._is_unknown_op_error(err, "create_task_run_persist"):
                        self._rust_native_persistence = False
                    else:
                        self._raise_from_rust_fsm_error(err)
                else:
                    persisted_by_rust = True
            if not persisted_by_rust:
                self._insert_task_row(task)
            self._persist_record(
                {
                    "record_type": "task_create",
                    "task_run_id": str(task.task_run_id),
                    "flow_run_id": str(task.flow_run_id),
                    "task_name": task.task_name,
                    "planned_node_id": task.planned_node_id,
                    "state": task.state.value,
                    "version": task.version,
                }
            )
            if (not self._rust_native_persistence) and self._rust_fsm_active():
                self._rust_register_task(task)
        return task

    def save_flow_manifest(
        self,
        run_id: UUID,
        manifest: dict[str, Any] | None,
        forecast: dict[str, Any] | None,
        warnings: list[str] | None,
        fallback_required: bool,
        source: str,
    ) -> None:
        manifest = manifest or {}
        nodes = manifest.get("nodes", [])
        task_to_ids: dict[str, list[str]] = {}
        for node in nodes:
            tn = node.get("task_name")
            nid = node.get("node_id")
            if tn and nid is not None:
                task_to_ids.setdefault(str(tn), []).append(str(nid))
        with self._lock:
            self._manifest_by_task[run_id] = task_to_ids
            manifest_json = json.dumps(manifest)
            forecast_json = json.dumps(forecast or {})
            warnings_json = json.dumps(warnings or [])
            persisted_by_rust = False
            if self._rust_fsm_active() and self._rust_native_persistence:
                out = self._rust_fsm_call(
                    "save_flow_manifest_persist",
                    {
                        **({} if self._rust_db_bound else {"db_path": str(self._sqlite_path)}),
                        "flow_run_id": str(run_id),
                        "manifest_json": manifest_json,
                        "forecast_json": forecast_json,
                        "warnings_json": warnings_json,
                        "fallback_required": bool(fallback_required),
                        "source": source,
                    },
                )
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if self._is_unknown_op_error(err, "save_flow_manifest_persist"):
                        self._rust_native_persistence = False
                    else:
                        self._raise_from_rust_fsm_error(err)
                else:
                    persisted_by_rust = True
            if not persisted_by_rust:
                self._sqlite_conn.execute(
                    """
                    INSERT OR REPLACE INTO dag_manifests
                    (flow_run_id, manifest_json, forecast_json, warnings_json, fallback_required, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(run_id),
                        manifest_json,
                        forecast_json,
                        warnings_json,
                        1 if fallback_required else 0,
                        source,
                        self._now(),
                    ],
                )

    def next_planned_node_id(self, flow_run_id: UUID, task_name: str) -> str | None:
        with self._lock:
            by_task = self._manifest_by_task.get(flow_run_id)
            if by_task is not None:
                used = {
                    str(t.planned_node_id)
                    for t in self._tasks.values()
                    if t.flow_run_id == flow_run_id and t.task_name == task_name and t.planned_node_id
                }
                for nid in by_task.get(task_name, []):
                    if nid not in used:
                        return nid
                return None
            return self._next_planned_from_sql_unlocked(flow_run_id, task_name)

    def _next_planned_from_sql_unlocked(self, flow_run_id: UUID, task_name: str) -> str | None:
        cur = self._sqlite_conn.execute(
            "SELECT manifest_json FROM dag_manifests WHERE flow_run_id = ? LIMIT 1",
            (str(flow_run_id),),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        manifest = json.loads(rows[0]["manifest_json"] or "{}")
        nodes = manifest.get("nodes", [])
        cur2 = self._sqlite_conn.execute(
            "SELECT planned_node_id FROM task_runs WHERE flow_run_id = ? AND task_name = ?",
            (str(flow_run_id), task_name),
        )
        used_rows = cur2.fetchall()
        used = {row["planned_node_id"] for row in used_rows if row["planned_node_id"]}
        for node in nodes:
            if node.get("task_name") == task_name and node.get("node_id") not in used:
                return str(node.get("node_id"))
        return None

    def _rebuild_manifest_cache_from_db(self) -> None:
        with self._lock:
            self._manifest_by_task.clear()
            cur = self._sqlite_conn.execute(
                "SELECT flow_run_id, manifest_json FROM dag_manifests",
            )
            for row in cur.fetchall():
                run_id = UUID(str(row["flow_run_id"]))
                manifest = json.loads(row["manifest_json"] or "{}")
                task_to_ids: dict[str, list[str]] = {}
                for node in manifest.get("nodes", []):
                    tn = node.get("task_name")
                    nid = node.get("node_id")
                    if tn and nid is not None:
                        task_to_ids.setdefault(str(tn), []).append(str(nid))
                self._manifest_by_task[run_id] = task_to_ids

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

            if self._rust_fsm_active():
                from_state = record.state.value
                body: dict[str, Any] = {
                    "run_id": str(run_id),
                    "to_state": to_state.value,
                    "transition_token": str(transition_token),
                    "transition_kind": transition_kind,
                }
                if expected_version is not None:
                    body["expected_version"] = int(expected_version)
                use_native_persist = self._rust_native_persistence
                op = "set_flow_state_persist" if use_native_persist else "set_flow_state"
                payload: dict[str, Any] = (
                    self._persist_payload(body) if use_native_persist else body
                )
                out = self._rust_fsm_call(op, payload)
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if use_native_persist and self._is_unknown_op_error(err, "set_flow_state_persist"):
                        self._rust_native_persistence = False
                        out = self._rust_fsm_call("set_flow_state", body)
                    if not out.get("ok", True):
                        self._raise_from_rust_fsm_error(out.get("error", {}))
                status = str(out["status"])
                new_state = RunState(str(out["current_state"]))
                new_version = int(out["version"])
                if status == "duplicate":
                    record.state = new_state
                    record.version = new_version
                    return SetStateResult(status="duplicate", state=new_state, version=new_version)

                self._tokens.add(transition_token)
                record.state = new_state
                record.version = new_version
                self._events.append(
                    {
                        "event_id": str(uuid4()),
                        "run_id": str(run_id),
                        "from_state": from_state,
                        "to_state": new_state.value,
                        "kind": transition_kind,
                    }
                )
                if not self._rust_native_persistence:
                    event = self._events[-1]
                    self._insert_event_row(event)
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
                        "to_state": new_state.value,
                        "kind": transition_kind,
                        "version": record.version,
                        "transition_token": str(transition_token),
                    }
                )
                return SetStateResult(status="applied", state=record.state, version=record.version)

            if transition_token in self._tokens:
                return SetStateResult(status="duplicate", state=record.state, version=record.version)

            if expected_version is not None and expected_version != record.version:
                raise ValueError(
                    f"version conflict expected={expected_version} actual={record.version}"
                )

            if not _legacy_is_valid_transition(record.state, to_state):
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

    def set_flow_states_batch(
        self,
        run_id: UUID,
        transitions: list[tuple[RunState, UUID, str, int | None]],
    ) -> list[SetStateResult]:
        if not transitions:
            return []
        if len(transitions) < self._FLOW_BATCH_MIN_SIZE:
            results: list[SetStateResult] = []
            for to_state, token, kind, expected_version in transitions:
                results.append(self.set_flow_state(run_id, to_state, token, kind, expected_version))
            return results
        with self._lock:
            if self._rust_fsm_active() and self._rust_native_persistence:
                record = self._flows[run_id]
                items: list[dict[str, Any]] = []
                for to_state, token, kind, expected_version in transitions:
                    req: dict[str, Any] = {
                        "run_id": str(run_id),
                        "to_state": to_state.value,
                        "transition_token": str(token),
                        "transition_kind": kind,
                    }
                    if expected_version is not None:
                        req["expected_version"] = int(expected_version)
                    items.append({"request": req})
                out = self._rust_fsm_call(
                    "set_flow_states_persist_batch",
                    {
                        **({} if self._rust_db_bound else {"db_path": str(self._sqlite_path)}),
                        "items": items,
                    },
                )
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if self._is_unknown_op_error(err, "set_flow_states_persist_batch"):
                        self._rust_native_persistence = False
                    else:
                        self._raise_from_rust_fsm_error(err)
            else:
                record = self._flows[run_id]
        # Fallback and non-native paths: defer to single-op API.
        if (not self._rust_fsm_active()) or (not self._rust_native_persistence):
            results: list[SetStateResult] = []
            for to_state, token, kind, expected_version in transitions:
                results.append(self.set_flow_state(run_id, to_state, token, kind, expected_version))
            return results
        # Native batch succeeded: synthesize local events/history.
        out_results = out.get("results", [])
        synthesized: list[SetStateResult] = []
        prev_state = record.state
        for i, result in enumerate(out_results):
            status = str(result.get("status", ""))
            new_state = RunState(str(result.get("current_state", record.state.value)))
            new_version = int(result.get("version", record.version))
            token = transitions[i][1]
            kind = transitions[i][2]
            if status == "duplicate":
                record.state = new_state
                record.version = new_version
                synthesized.append(SetStateResult(status="duplicate", state=new_state, version=new_version))
                prev_state = record.state
                continue
            self._tokens.add(token)
            self._events.append(
                {
                    "event_id": str(uuid4()),
                    "run_id": str(run_id),
                    "from_state": prev_state.value,
                    "to_state": new_state.value,
                    "kind": kind,
                }
            )
            record.state = new_state
            record.version = new_version
            self._persist_record(
                {
                    "record_type": "flow_transition",
                    "run_id": str(run_id),
                    "to_state": new_state.value,
                    "kind": kind,
                    "version": record.version,
                    "transition_token": str(token),
                }
            )
            synthesized.append(SetStateResult(status="applied", state=new_state, version=new_version))
            prev_state = record.state
        return synthesized

    def get_flow(self, run_id: UUID) -> FlowRunRecord:
        with self._lock:
            return self._flows[run_id]

    def get_task_run(self, task_run_id: UUID) -> TaskRunRecord:
        with self._lock:
            return self._tasks[task_run_id]

    def latest_flow(self) -> FlowRunRecord | None:
        with self._lock:
            if self._latest_flow_run_id is None:
                return None
            return self._flows[self._latest_flow_run_id]

    def record_task_event(self, task_run_id: UUID, event_type: str, data: dict[str, Any] | None = None) -> None:
        event_to_state: dict[str, RunState] = {
            "task_pending": RunState.PENDING,
            "task_running": RunState.RUNNING,
            "task_completed": RunState.COMPLETED,
            "task_failed": RunState.FAILED,
        }
        with self._lock:
            task = self._tasks[task_run_id]
            transition_token: UUID | None = None
            from_state: str | None = None

            if self._rust_fsm_active() and event_type in event_to_state:
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
                op = "set_task_state_persist" if use_native_persist else "set_task_state"
                payload: dict[str, Any] = (
                    self._persist_payload(req, event_type=event_type, data=data or {})
                    if use_native_persist
                    else req
                )
                out = self._rust_fsm_call(op, payload)
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if use_native_persist and self._is_unknown_op_error(err, "set_task_state_persist"):
                        self._rust_native_persistence = False
                        out = self._rust_fsm_call("set_task_state", req)
                    if not out.get("ok", True):
                        self._raise_from_rust_fsm_error(out.get("error", {}))
                status = str(out["status"])
                if status == "duplicate":
                    task.state = RunState(str(out["current_state"]))
                    task.version = int(out["version"])
                    return
                task.state = RunState(str(out["current_state"]))
                task.version = int(out["version"])
            else:
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
                from_state = None

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
                        **({} if self._rust_db_bound else {"db_path": str(self._sqlite_path)}),
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
                    task.state = RunState(str(result.get("current_state", task.state.value)))
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

    def list_flow_runs(self, state: str | None = None, limit: int = 50, cursor: str | None = None) -> PageResult:
        rust_result = self._query_rust("flow_runs", {"state": state, "limit": limit, "cursor": cursor})
        if rust_result is not None:
            return PageResult(items=rust_result["items"], next_cursor=rust_result["next_cursor"])
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
        rust_result = self._query_rust("flow_run_detail", {"flow_run_id": str(flow_run_id)})
        if rust_result is not None:
            return rust_result
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
        rust_result = self._query_rust(
            "task_runs",
            {"flow_run_id": str(flow_run_id), "limit": limit, "cursor": cursor},
        )
        if rust_result is not None:
            return PageResult(items=rust_result["items"], next_cursor=rust_result["next_cursor"])
        query = (
            "SELECT seq,id,flow_run_id,task_name,planned_node_id,state,version,created_at,updated_at "
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
        rust_result = self._query_rust(
            "logs",
            {
                "flow_run_id": str(flow_run_id),
                "task_run_id": str(task_run_id) if task_run_id else None,
                "level": level.upper() if level else None,
                "limit": limit,
                "cursor": cursor,
            },
        )
        if rust_result is not None:
            return PageResult(items=rust_result["items"], next_cursor=rust_result["next_cursor"])
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
        rust_result = self._query_rust("flows", {"limit": limit, "cursor": cursor})
        if rust_result is not None:
            return PageResult(items=rust_result["items"], next_cursor=rust_result["next_cursor"])
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
        rust_result = self._query_rust("tasks", {"flow_name": flow_name, "limit": limit})
        if rust_result is not None:
            return rust_result
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
        rust_result = self._query_rust(
            "events",
            {"flow_run_id": str(flow_run_id), "limit": limit, "cursor": cursor},
        )
        if rust_result is not None:
            return PageResult(items=rust_result["items"], next_cursor=rust_result["next_cursor"])
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
        rust_result = self._query_rust("artifacts_flow", {"flow_run_id": str(flow_run_id), "limit": limit})
        if rust_result is not None:
            return rust_result
        rows = self._query_rows(
            "SELECT id,flow_run_id,task_run_id,artifact_type,key,summary,created_at "
            "FROM artifacts WHERE flow_run_id = ? ORDER BY created_at DESC LIMIT ?",
            [str(flow_run_id), limit],
        )
        return [self._artifact_row_to_dict(r) for r in rows]

    def list_artifacts_for_task(self, task_run_id: UUID, limit: int = 200) -> list[dict[str, Any]]:
        rust_result = self._query_rust("artifacts_task", {"task_run_id": str(task_run_id), "limit": limit})
        if rust_result is not None:
            return rust_result
        rows = self._query_rows(
            "SELECT id,flow_run_id,task_run_id,artifact_type,key,summary,created_at "
            "FROM artifacts WHERE task_run_id = ? ORDER BY created_at DESC LIMIT ?",
            [str(task_run_id), limit],
        )
        return [self._artifact_row_to_dict(r) for r in rows]

    def get_artifact(self, artifact_id: UUID) -> dict[str, Any] | None:
        rust_result = self._query_rust("artifact", {"artifact_id": str(artifact_id)})
        if rust_result is not None:
            return rust_result
        rows = self._query_rows(
            "SELECT id,flow_run_id,task_run_id,artifact_type,key,summary,created_at "
            "FROM artifacts WHERE id = ? LIMIT 1",
            [str(artifact_id)],
        )
        return self._artifact_row_to_dict(rows[0]) if rows else None

    def create_deployment(
        self,
        name: str,
        flow_name: str,
        entrypoint: str | None = None,
        path: str | None = None,
        default_parameters: dict[str, Any] | None = None,
        paused: bool = False,
        concurrency_limit: int | None = None,
        collision_strategy: str = "ENQUEUE",
        schedule_interval_seconds: int | None = None,
        schedule_cron: str | None = None,
        schedule_next_run_at: str | None = None,
        schedule_enabled: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "flow_name": flow_name,
            "entrypoint": entrypoint,
            "path": path,
            "default_parameters": default_parameters or {},
            "paused": paused,
            "concurrency_limit": concurrency_limit,
            "collision_strategy": collision_strategy,
            "schedule_interval_seconds": schedule_interval_seconds,
            "schedule_cron": schedule_cron,
            "schedule_next_run_at": schedule_next_run_at,
            "schedule_enabled": schedule_enabled,
        }
        rust = self._rust_deployment_dispatch("deployment_create", body)
        if rust is not None and rust.get("ok") and rust.get("deployment") is not None:
            return self._deployment_from_rust_json(rust["deployment"])
        if rust is not None and rust.get("ok") is False:
            err = rust.get("error") or {}
            raise ValueError(str(err.get("message", "deployment_create failed")))

        with self._lock:
            existing = self._query_rows(
                """
                SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                       concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,
                       schedule_next_run_at,schedule_enabled,created_at,updated_at
                FROM deployments
                WHERE name = ?
                LIMIT 1
                """,
                [name],
            )
            if existing:
                return self._deployment_row_to_dict(existing[0])

            si = schedule_interval_seconds
            sc = schedule_cron
            if sc and str(sc).strip():
                si = None
            elif si is not None and si > 0:
                sc = None

            sched_next = schedule_next_run_at
            if schedule_enabled and si and si > 0 and sched_next is None:
                sched_next = self._now()
            if schedule_enabled and sc and str(sc).strip() and sched_next is None:
                raise ValueError(
                    "Cron schedules require the Rust engine (bind_db) to compute the first schedule_next_run_at, "
                    "or pass schedule_next_run_at explicitly."
                )

            now = self._now()
            deployment_id = str(uuid4())
            self._sqlite_conn.execute(
                """
                INSERT INTO deployments
                (id,name,flow_name,entrypoint,path,default_parameters,paused,
                 concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,
                 schedule_next_run_at,schedule_enabled,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    deployment_id,
                    name,
                    flow_name,
                    entrypoint,
                    path,
                    json.dumps(default_parameters or {}),
                    1 if paused else 0,
                    concurrency_limit,
                    collision_strategy,
                    si,
                    sc,
                    sched_next,
                    1 if schedule_enabled else 0,
                    now,
                    now,
                ],
            )
            row = self._query_rows(
                """
                SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                       concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,
                       schedule_next_run_at,schedule_enabled,created_at,updated_at
                FROM deployments
                WHERE id = ?
                LIMIT 1
                """,
                [deployment_id],
            )[0]
            return self._deployment_row_to_dict(row)

    def update_deployment(self, deployment_id: UUID, patch: dict[str, Any]) -> dict[str, Any]:
        body = dict(patch)
        body["deployment_id"] = str(deployment_id)
        rust = self._rust_deployment_dispatch("deployment_update", body)
        if rust is not None and rust.get("ok") and rust.get("deployment") is not None:
            return self._deployment_from_rust_json(rust["deployment"])
        if rust is not None and rust.get("ok") is False:
            err = rust.get("error") or {}
            msg = str(err.get("message", "deployment_update failed"))
            if err.get("code") == "not_found":
                raise ValueError("deployment not found")
            raise ValueError(msg)

        return self._update_deployment_python(deployment_id, patch)

    def _update_deployment_python(self, deployment_id: UUID, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            dep = self.get_deployment(deployment_id)
            if dep is None:
                raise ValueError("deployment not found")
            entrypoint = dep.get("entrypoint")
            path = dep.get("path")
            default_parameters = dict(dep.get("default_parameters") or {})
            paused = bool(dep.get("paused"))
            concurrency_limit = dep.get("concurrency_limit")
            collision_strategy = str(dep.get("collision_strategy") or "ENQUEUE")
            schedule_interval_seconds = dep.get("schedule_interval_seconds")
            schedule_cron = dep.get("schedule_cron")
            schedule_next_run_at = dep.get("schedule_next_run_at")
            schedule_enabled = bool(dep.get("schedule_enabled"))

            if "entrypoint" in patch:
                v = patch["entrypoint"]
                entrypoint = None if v is None else str(v)
            if "path" in patch:
                v = patch["path"]
                path = None if v is None else str(v)
            if "default_parameters" in patch and patch["default_parameters"] is not None:
                default_parameters = dict(patch["default_parameters"])
            if "paused" in patch:
                paused = bool(patch["paused"])
            if "concurrency_limit" in patch:
                concurrency_limit = patch["concurrency_limit"]
            if "collision_strategy" in patch and patch["collision_strategy"] is not None:
                collision_strategy = str(patch["collision_strategy"])
            if "schedule_interval_seconds" in patch:
                schedule_interval_seconds = patch["schedule_interval_seconds"]
            if "schedule_cron" in patch:
                schedule_cron = patch["schedule_cron"]
            if "schedule_next_run_at" in patch:
                schedule_next_run_at = patch["schedule_next_run_at"]
            if "schedule_enabled" in patch:
                schedule_enabled = bool(patch["schedule_enabled"])

            if schedule_cron and str(schedule_cron).strip():
                schedule_interval_seconds = None
            elif schedule_interval_seconds is not None and int(schedule_interval_seconds) > 0:
                schedule_cron = None

            if schedule_enabled and schedule_interval_seconds and int(schedule_interval_seconds) > 0 and not schedule_next_run_at:
                schedule_next_run_at = self._now()
            if (
                schedule_enabled
                and schedule_cron
                and str(schedule_cron).strip()
                and not schedule_next_run_at
            ):
                raise ValueError(
                    "Cron schedules require schedule_next_run_at when the Rust kernel is unavailable, "
                    "or use the native engine with bind_db."
                )

            ts = self._now()
            self._sqlite_conn.execute(
                """
                UPDATE deployments SET
                  entrypoint = ?, path = ?, default_parameters = ?, paused = ?,
                  concurrency_limit = ?, collision_strategy = ?,
                  schedule_interval_seconds = ?, schedule_cron = ?, schedule_next_run_at = ?,
                  schedule_enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                [
                    entrypoint,
                    path,
                    json.dumps(default_parameters),
                    1 if paused else 0,
                    concurrency_limit,
                    collision_strategy,
                    schedule_interval_seconds,
                    schedule_cron,
                    schedule_next_run_at,
                    1 if schedule_enabled else 0,
                    ts,
                    str(deployment_id),
                ],
            )
            row = self._query_rows(
                """
                SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                       concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,
                       schedule_next_run_at,schedule_enabled,created_at,updated_at
                FROM deployments WHERE id = ?
                LIMIT 1
                """,
                [str(deployment_id)],
            )[0]
            return self._deployment_row_to_dict(row)

    def list_deployments(self, limit: int = 200, cursor: str | None = None) -> PageResult:
        query = (
            "SELECT seq,id,name,flow_name,entrypoint,path,default_parameters,paused,"
            " concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,"
            " schedule_next_run_at,schedule_enabled,created_at,updated_at "
            "FROM deployments"
        )
        params: list[Any] = []
        if cursor:
            query += " WHERE seq < ?"
            params.append(int(cursor))
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._deployment_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def get_deployment(self, deployment_id: UUID) -> dict[str, Any] | None:
        rows = self._query_rows(
            """
            SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                   concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,
                   schedule_next_run_at,schedule_enabled,created_at,updated_at
            FROM deployments
            WHERE id = ?
            LIMIT 1
            """,
            [str(deployment_id)],
        )
        if not rows:
            return None
        return self._deployment_row_to_dict(rows[0])

    def trigger_deployment_run(
        self,
        deployment_id: UUID,
        parameters: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        rust = self._rust_deployment_dispatch(
            "deployment_trigger_run",
            {
                "deployment_id": str(deployment_id),
                "parameters": parameters,
                "idempotency_key": idempotency_key,
            },
        )
        if rust is not None:
            if rust.get("ok"):
                return rust["run"]
            err = rust.get("error") or {}
            code = err.get("code", "")
            msg = str(err.get("message", ""))
            if code == "not_found":
                raise ValueError("deployment not found")
            if code == "paused":
                raise ValueError("deployment is paused")
            raise ValueError(msg or "deployment trigger failed")

        with self._lock:
            dep = self.get_deployment(deployment_id)
            if dep is None:
                raise ValueError("deployment not found")
            if dep["paused"]:
                raise ValueError("deployment is paused")

            if idempotency_key:
                existing = self._query_rows(
                    """
                    SELECT seq,id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
                           worker_name,lease_until,flow_run_id,error,created_at,updated_at,started_at,finished_at
                    FROM deployment_runs
                    WHERE deployment_id = ? AND idempotency_key = ?
                    LIMIT 1
                    """,
                    [str(deployment_id), idempotency_key],
                )
                if existing:
                    return self._deployment_run_row_to_dict(existing[0])

            requested = parameters or {}
            resolved = dict(dep.get("default_parameters", {}))
            resolved.update(requested)
            strategy = (dep.get("collision_strategy") or "ENQUEUE").upper()
            limit = dep.get("concurrency_limit")
            status = "SCHEDULED"
            err_msg: str | None = None
            if limit is not None and strategy == "CANCEL_NEW" and self._count_exec_runs(str(deployment_id)) >= int(
                limit
            ):
                status = "CANCELLED"
                err_msg = "concurrency limit reached"
            now = self._now()
            run_id = str(uuid4())
            self._sqlite_conn.execute(
                """
                INSERT INTO deployment_runs
                (id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
                 worker_name,lease_until,flow_run_id,error,created_at,updated_at,started_at,finished_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    run_id,
                    str(deployment_id),
                    status,
                    json.dumps(requested),
                    json.dumps(resolved),
                    idempotency_key,
                    None,
                    None,
                    None,
                    err_msg,
                    now,
                    now,
                    None,
                    None,
                ],
            )
            row = self._query_rows(
                """
                SELECT seq,id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
                       worker_name,lease_until,flow_run_id,error,created_at,updated_at,started_at,finished_at
                FROM deployment_runs
                WHERE id = ?
                LIMIT 1
                """,
                [run_id],
            )[0]
            return self._deployment_run_row_to_dict(row)

    def list_deployment_runs(
        self, deployment_id: UUID | None = None, limit: int = 200, cursor: str | None = None
    ) -> PageResult:
        query = (
            "SELECT seq,id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,"
            " worker_name,lease_until,flow_run_id,error,created_at,updated_at,started_at,finished_at "
            "FROM deployment_runs"
        )
        conditions: list[str] = []
        params: list[Any] = []
        if deployment_id:
            conditions.append("deployment_id = ?")
            params.append(str(deployment_id))
        if cursor:
            conditions.append("seq < ?")
            params.append(int(cursor))
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._deployment_run_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def claim_next_deployment_run(self, worker_name: str, lease_seconds: int = 30) -> dict[str, Any] | None:
        rust = self._rust_deployment_dispatch(
            "deployment_claim_next",
            {"worker_name": worker_name, "lease_seconds": lease_seconds},
        )
        if rust is not None:
            if rust.get("ok"):
                run = rust.get("run")
                return None if run is None else run
            err = rust.get("error") or {}
            raise RuntimeError(str(err.get("message", "deployment claim failed")))

        with self._lock:
            self._reclaim_expired_claims_python()
            now_dt = datetime.now(UTC)
            now = now_dt.isoformat()
            lease_until = (now_dt + timedelta(seconds=max(1, lease_seconds))).isoformat()
            self._sqlite_conn.execute(
                """
                INSERT INTO workers(name,last_heartbeat,status,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    last_heartbeat=excluded.last_heartbeat,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                [worker_name, now, "ONLINE", now],
            )
            candidates = self._query_rows(
                """
                SELECT dr.id FROM deployment_runs dr
                INNER JOIN deployments d ON d.id = dr.deployment_id
                WHERE dr.status = 'SCHEDULED'
                AND (
                  d.concurrency_limit IS NULL
                  OR (
                    SELECT COUNT(*) FROM deployment_runs x
                    WHERE x.deployment_id = dr.deployment_id
                    AND x.status IN ('CLAIMED','RUNNING')
                  ) < d.concurrency_limit
                )
                ORDER BY dr.created_at ASC
                LIMIT 1
                """,
                [],
            )
            if not candidates:
                return None
            candidate_id = candidates[0]["id"]
            self._sqlite_conn.execute(
                """
                UPDATE deployment_runs
                SET status = 'CLAIMED', worker_name = ?, lease_until = ?, updated_at = ?
                WHERE id = ? AND status = 'SCHEDULED'
                """,
                [worker_name, lease_until, now, candidate_id],
            )
            row = self._query_rows(
                """
                SELECT seq,id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
                       worker_name,lease_until,flow_run_id,error,created_at,updated_at,started_at,finished_at
                FROM deployment_runs
                WHERE id = ? AND status = 'CLAIMED'
                LIMIT 1
                """,
                [candidate_id],
            )
            if not row:
                return None
            return self._deployment_run_row_to_dict(row[0])

    def claim_next_deployment_run_wait(
        self, worker_name: str, lease_seconds: int = 30, wait_ms: int = 500
    ) -> dict[str, Any] | None:
        rust = self._rust_deployment_dispatch(
            "deployment_claim_next_wait",
            {"worker_name": worker_name, "lease_seconds": lease_seconds, "wait_ms": wait_ms},
        )
        if rust is not None:
            if rust.get("ok"):
                run = rust.get("run")
                return None if run is None else run
            err = rust.get("error") or {}
            raise RuntimeError(str(err.get("message", "deployment claim wait failed")))

        deadline = time.monotonic() + max(wait_ms, 1) / 1000.0
        while time.monotonic() < deadline:
            c = self.claim_next_deployment_run(worker_name, lease_seconds)
            if c is not None:
                return c
            time.sleep(0.05)
        return None

    def mark_deployment_run_started(self, deployment_run_id: UUID) -> None:
        rust = self._rust_deployment_dispatch(
            "deployment_mark_run_started", {"deployment_run_id": str(deployment_run_id)}
        )
        if rust is not None and rust.get("ok"):
            return
        now = self._now()
        with self._lock:
            self._sqlite_conn.execute(
                """
                UPDATE deployment_runs
                SET status = 'RUNNING', started_at = ?, updated_at = ?
                WHERE id = ?
                """,
                [now, now, str(deployment_run_id)],
            )

    def mark_deployment_run_finished(
        self, deployment_run_id: UUID, status: str, flow_run_id: UUID | None = None, error: str | None = None
    ) -> None:
        rust = self._rust_deployment_dispatch(
            "deployment_mark_run_finished",
            {
                "deployment_run_id": str(deployment_run_id),
                "status": status,
                "flow_run_id": str(flow_run_id) if flow_run_id else None,
                "error": error,
            },
        )
        if rust is not None and rust.get("ok"):
            return
        now = self._now()
        with self._lock:
            self._sqlite_conn.execute(
                """
                UPDATE deployment_runs
                SET status = ?, flow_run_id = ?, error = ?, finished_at = ?, updated_at = ?, lease_until = NULL
                WHERE id = ?
                """,
                [status, str(flow_run_id) if flow_run_id else None, error, now, now, str(deployment_run_id)],
            )

    def get_flow_run_dag(self, flow_run_id: UUID, mode: str = "logical") -> dict[str, Any]:
        manifest_rows = self._query_rows(
            "SELECT manifest_json, forecast_json, warnings_json, fallback_required, source FROM dag_manifests WHERE flow_run_id = ? LIMIT 1",
            [str(flow_run_id)],
        )
        task_rows = self._query_rows(
            """
            SELECT id, task_name, planned_node_id, state, created_at, updated_at
            FROM task_runs
            WHERE flow_run_id = ?
            ORDER BY created_at ASC
            """,
            [str(flow_run_id)],
        )

        if manifest_rows:
            manifest_raw = manifest_rows[0]
            manifest = json.loads(manifest_raw["manifest_json"] or "{}")
            forecast = json.loads(manifest_raw["forecast_json"] or "{}")
            warnings = json.loads(manifest_raw["warnings_json"] or "[]")
            fallback_required = bool(manifest_raw["fallback_required"])
            source = manifest_raw["source"]
        else:
            manifest = {"nodes": [], "edges": []}
            forecast = {}
            warnings = ["No precomputed forecast manifest available for run."]
            fallback_required = True
            source = "runtime"

        if not manifest.get("nodes"):
            manifest = self._infer_runtime_manifest(task_rows)
            source = "runtime"
            fallback_required = True
            warnings = warnings + ["Using runtime-inferred DAG."]

        if mode == "expanded":
            nodes, edges = self._expanded_dag(manifest, task_rows)
        else:
            nodes, edges = self._logical_dag(manifest, task_rows)
        return {
            "flow_run_id": str(flow_run_id),
            "mode": mode,
            "source": source,
            "fallback_required": fallback_required,
            "warnings": warnings,
            "forecast": forecast,
            "nodes": nodes,
            "edges": edges,
        }

    def _query_rust(self, kind: str, params: dict[str, Any]) -> Any | None:
        if self._rust_bridge is None:
            return None
        try:
            return self._rust_bridge.query(str(self._sqlite_path), kind, params)
        except Exception:
            return None

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
                    log_level = "ERROR" if rec.get("event_type") == "task_failed" else "INFO"
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

        if record_type in {"flow_transition", "task_event"}:
            # Rebuild in-memory event stream from persisted history.
            self._events.append(rec)

    def _read_db_empty_unlocked(self) -> bool:
        row = self._sqlite_conn.execute("SELECT COUNT(1) AS count FROM flow_runs").fetchone()
        if row is None:
            return True
        return int(row["count"]) == 0

    def _init_sqlite_schema(self, conn: sqlite3.Connection) -> None:
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
                planned_node_id TEXT,
                state TEXT NOT NULL,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dag_manifests (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                flow_run_id TEXT UNIQUE NOT NULL,
                manifest_json TEXT NOT NULL,
                forecast_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                fallback_required INTEGER NOT NULL,
                source TEXT NOT NULL,
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
            CREATE TABLE IF NOT EXISTS deployments (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                name TEXT UNIQUE NOT NULL,
                flow_name TEXT NOT NULL,
                entrypoint TEXT,
                path TEXT,
                default_parameters TEXT NOT NULL,
                paused INTEGER NOT NULL,
                concurrency_limit INTEGER,
                collision_strategy TEXT NOT NULL DEFAULT 'ENQUEUE',
                schedule_interval_seconds INTEGER,
                schedule_cron TEXT,
                schedule_next_run_at TEXT,
                schedule_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deployment_runs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                deployment_id TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_parameters TEXT NOT NULL,
                resolved_parameters TEXT NOT NULL,
                idempotency_key TEXT,
                worker_name TEXT,
                lease_until TEXT,
                flow_run_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_deployment_runs_idempotency
                ON deployment_runs(deployment_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL;
            CREATE TABLE IF NOT EXISTS workers (
                name TEXT PRIMARY KEY,
                last_heartbeat TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
            CREATE INDEX IF NOT EXISTS idx_deployments_name
                ON deployments(name);
            CREATE INDEX IF NOT EXISTS idx_deployment_runs_status_created
                ON deployment_runs(status, created_at ASC);
            CREATE INDEX IF NOT EXISTS idx_deployment_runs_deployment_created
                ON deployment_runs(deployment_id, created_at DESC);
            """
        )

    def _ensure_schema_upgrades(self, conn: sqlite3.Connection) -> None:
        cols = conn.execute("PRAGMA table_info(task_runs)").fetchall()
        col_names = {col["name"] for col in cols}
        if "planned_node_id" not in col_names:
            conn.execute("ALTER TABLE task_runs ADD COLUMN planned_node_id TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_runs_flow_planned ON task_runs(flow_run_id, planned_node_id)"
        )
        dep_cols = {c["name"] for c in conn.execute("PRAGMA table_info(deployments)").fetchall()}
        if "concurrency_limit" not in dep_cols:
            conn.execute("ALTER TABLE deployments ADD COLUMN concurrency_limit INTEGER")
        if "collision_strategy" not in dep_cols:
            conn.execute(
                "ALTER TABLE deployments ADD COLUMN collision_strategy TEXT NOT NULL DEFAULT 'ENQUEUE'"
            )
        if "schedule_interval_seconds" not in dep_cols:
            conn.execute("ALTER TABLE deployments ADD COLUMN schedule_interval_seconds INTEGER")
        if "schedule_cron" not in dep_cols:
            conn.execute("ALTER TABLE deployments ADD COLUMN schedule_cron TEXT")
        if "schedule_next_run_at" not in dep_cols:
            conn.execute("ALTER TABLE deployments ADD COLUMN schedule_next_run_at TEXT")
        if "schedule_enabled" not in dep_cols:
            conn.execute("ALTER TABLE deployments ADD COLUMN schedule_enabled INTEGER NOT NULL DEFAULT 0")

    def _query_rows(self, query: str, params: list[Any]) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._sqlite_conn.execute(query, params)
            return list(cur.fetchall())

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _insert_flow_row(self, record: FlowRunRecord) -> None:
        now = self._now()
        self._sqlite_conn.execute(
            "INSERT OR IGNORE INTO flow_runs(id,name,state,version,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            [str(record.run_id), record.name, record.state.value, record.version, now, now],
        )

    def _update_flow_row(self, record: FlowRunRecord) -> None:
        self._sqlite_conn.execute(
            "UPDATE flow_runs SET state = ?, version = ?, updated_at = ? WHERE id = ?",
            [record.state.value, record.version, self._now(), str(record.run_id)],
        )

    def _insert_task_row(self, task: TaskRunRecord) -> None:
        now = self._now()
        self._sqlite_conn.execute(
            "INSERT OR IGNORE INTO task_runs(id,flow_run_id,task_name,planned_node_id,state,version,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [
                str(task.task_run_id),
                str(task.flow_run_id),
                task.task_name,
                task.planned_node_id,
                task.state.value,
                task.version,
                now,
                now,
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

    def _insert_log_row(self, log: dict[str, Any]) -> None:
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

    def _infer_runtime_manifest(self, task_rows: list[sqlite3.Row]) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []
        seen: dict[str, str] = {}
        previous: str | None = None
        counter = 0
        for row in task_rows:
            task_name = row["task_name"]
            if task_name not in seen:
                counter += 1
                node_id = f"rt_{counter}"
                seen[task_name] = node_id
                nodes.append({"node_id": node_id, "task_name": task_name, "op_type": "runtime", "deps": []})
                if previous is not None:
                    edges.append({"from": previous, "to": node_id})
                previous = node_id
        return {"nodes": nodes, "edges": edges}

    def _logical_dag(
        self, manifest: dict[str, Any], task_rows: list[sqlite3.Row]
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        nodes = manifest.get("nodes", [])
        edges = manifest.get("edges", [])
        by_planned: dict[str, list[str]] = {}
        by_task_name: dict[str, list[str]] = {}
        for row in task_rows:
            state = row["state"]
            planned = row["planned_node_id"]
            task_name = row["task_name"]
            if planned:
                by_planned.setdefault(planned, []).append(state)
            by_task_name.setdefault(task_name, []).append(state)

        out_nodes: list[dict[str, Any]] = []
        node_state: dict[str, str] = {}
        for node in nodes:
            node_id = str(node["node_id"])
            states = by_planned.get(node_id) or by_task_name.get(str(node.get("task_name")), [])
            state = self._aggregate_state(states)
            node_state[node_id] = state
            out_nodes.append(
                {
                    "id": node_id,
                    "label": node.get("task_name", node_id),
                    "task_name": node.get("task_name"),
                    "op_type": node.get("op_type"),
                    "state": state,
                }
            )

        upstreams: dict[str, list[str]] = {}
        for edge in edges:
            upstreams.setdefault(edge["to"], []).append(edge["from"])
        for node in out_nodes:
            if node["state"] in {"PENDING", "SCHEDULED"}:
                if any(
                    node_state.get(src) in {"FAILED", "CANCELLED", "NOT_REACHABLE"}
                    for src in upstreams.get(node["id"], [])
                ):
                    node["state"] = "NOT_REACHABLE"
                    node_state[node["id"]] = "NOT_REACHABLE"
        return out_nodes, edges

    def _expanded_dag(
        self, manifest: dict[str, Any], task_rows: list[sqlite3.Row]
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        max_nodes = 600
        limited_rows = task_rows[:max_nodes]
        nodes = [
            {
                "id": row["id"],
                "label": f"{row['task_name']}:{str(row['id'])[:8]}",
                "task_name": row["task_name"],
                "planned_node_id": row["planned_node_id"],
                "state": row["state"],
            }
            for row in limited_rows
        ]
        by_planned_runs: dict[str, list[str]] = {}
        for row in limited_rows:
            if row["planned_node_id"]:
                by_planned_runs.setdefault(row["planned_node_id"], []).append(row["id"])
        manifest_edges = manifest.get("edges", [])
        edges: list[dict[str, str]] = []
        for edge in manifest_edges:
            src_runs = by_planned_runs.get(edge["from"], [])
            dst_runs = by_planned_runs.get(edge["to"], [])
            for src in src_runs:
                for dst in dst_runs:
                    edges.append({"from": src, "to": dst})
                    if len(edges) >= 2000:
                        return nodes, edges
        if not edges:
            for i in range(1, len(nodes)):
                edges.append({"from": nodes[i - 1]["id"], "to": nodes[i]["id"]})
        return nodes, edges

    def _aggregate_state(self, states: list[str]) -> str:
        if not states:
            return "PENDING"
        priority = ["FAILED", "CANCELLED", "RUNNING", "PENDING", "SCHEDULED", "COMPLETED"]
        for state in priority:
            if state in states:
                return state
        return states[-1]

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
            "planned_node_id": row["planned_node_id"],
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
            "schedule_next_run_at": col("schedule_next_run_at"),
            "schedule_enabled": bool(col("schedule_enabled", 0)),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _deployment_run_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
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
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }


def _legacy_is_valid_transition(from_state: RunState, to_state: RunState) -> bool:
    allowed: dict[RunState, set[RunState]] = {
        RunState.SCHEDULED: {RunState.PENDING, RunState.CANCELLED},
        RunState.PENDING: {RunState.RUNNING, RunState.CANCELLED},
        RunState.RUNNING: {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED},
        RunState.COMPLETED: set(),
        RunState.FAILED: set(),
        RunState.CANCELLED: set(),
    }
    return to_state in allowed[from_state]
