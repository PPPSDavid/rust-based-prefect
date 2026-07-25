from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from .persistence import (
    DEFAULT_WORK_POOL_ID,
    ControlPlaneStore,
    create_store,
    resolve_sqlite_path,
)
from .result_codec import (
    ResultEncodeError,
    decode_task_result,
    encode_task_result,
)

_RustQueryBridge: Any = None
_RustFsmBridge: Any = None
try:
    from .rust_bridge import (
        RustFsmBridge as _RustFsmBridge_cls,
        RustQueryBridge as _RustQueryBridge_cls,
    )

    _RustQueryBridge = _RustQueryBridge_cls
    _RustFsmBridge = _RustFsmBridge_cls
except Exception:  # pragma: no cover - best-effort optional accelerator
    pass

RustQueryBridge: Any = _RustQueryBridge
RustFsmBridge: Any = _RustFsmBridge

SUBFLOW_MAX_DEPTH = 32


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
    next_cursor: str | None


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


class InMemoryControlPlane:
    _FLOW_BATCH_MIN_SIZE = 2
    _TASK_BATCH_MIN_SIZE = 2

    """
    Python control plane (Rust-accelerated when native lib + ``bind_db`` are available).

    Backed by optional in-process Rust FSM + SQLite persistence via ``rust_bridge``
    (ctypes FFI). This is used by the shim and optional FastAPI server.

    Concurrency: writes are serialized under ``_lock``. List APIs and the initial Rust
    read in ``get_flow_run_detail`` use lock-free WAL SQLite readers when the query
    bridge is available; detail enrichment still takes ``_lock`` (see
    ``docs/perf_methodology.md``).
    """

    def __init__(self, history_path: str | None = None) -> None:
        self._flows: dict[UUID, FlowRunRecord] = {}
        self._tasks: dict[UUID, TaskRunRecord] = {}
        self._flow_results: dict[UUID, Any] = {}
        self._events: list[dict[str, Any]] = []
        self._tokens: set[UUID] = set()
        self._lock = RLock()
        self._latest_flow_run_id: UUID | None = None
        self._pending_resume_from: UUID | None = None
        self._resume_lookups_enabled: bool = False
        self._resume_schema_ready: bool = False
        self._task_result_cache_ready: bool = False
        self._history_path = Path(history_path) if history_path else None
        self._store: ControlPlaneStore = create_store(history_path=self._history_path)
        # Keep path/conn attributes for tests and Rust bind_db (SQLite path / PG adapter).
        self._sqlite_path = self._store.path or resolve_sqlite_path(self._history_path)
        self._sqlite_conn = self._store.connection
        self._manifest_by_task: dict[UUID, dict[str, list[str]]] = {}
        self._reserved_planned_ids: dict[UUID, set[str]] = {}
        # Operator lifecycle metadata (pause/cancel); not gate waits.
        self._lifecycle_by_flow: dict[str, dict[str, Any]] = {}
        self._ensure_default_work_pool()
        self._replay_to_sqlite = self._read_db_empty_unlocked()
        # Apply resume DDL before Rust bind_db so ALTER/CREATE cannot race the native handle.
        self._ensure_resume_schema()
        self._rust_bridge = None
        self._rust_fsm_bridge = None
        self._rust_fsm_handle = 0
        self._rust_native_persistence = True
        self._rust_db_bound = False
        self._test_plane_ref: InMemoryControlPlane | None = None
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
                    if self._store.backend_kind == "postgres":
                        pg_url = getattr(self._store, "database_url", None)
                        if not pg_url:
                            raise RuntimeError("postgres store missing database_url")
                        bind_out = self._rust_fsm_call(
                            "bind_db", {"database_url": str(pg_url)}
                        )
                    else:
                        bind_out = self._rust_fsm_call(
                            "bind_db", {"db_path": str(self._sqlite_path)}
                        )
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
        # When bind_db is active, Rust and Python share one SQLite file via separate
        # connections. Serialize all Rust FFI with Python writes on ``_lock``.
        if self._rust_db_bound:
            with self._lock:
                return bridge.control(handle, op, body)
        return bridge.control(handle, op, body)

    def _persist_payload(
        self, request: dict[str, Any], **extras: Any
    ) -> dict[str, Any]:
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

    def _rust_deployment_dispatch(
        self, op: str, body: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Invoke Rust deployment ops on the bound SQLite connection. None = use Python fallback."""
        if not self._rust_fsm_active() or not self._rust_db_bound:
            if (
                self._rust_fsm_active()
                and not self._rust_db_bound
                and not self._warned_deployment_fallback
            ):
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

    def _gcl_dispatch(self, op: str, body: dict[str, Any]) -> dict[str, Any] | None:
        """Invoke Rust GCL ops on the bound SQLite connection. None = Python fallback."""
        if not self._rust_fsm_active() or not self._rust_db_bound:
            return None
        try:
            out = self._rust_fsm_call(op, body)
        except Exception:
            return None
        if not out.get("ok", True):
            err = out.get("error") or {}
            if isinstance(err, dict) and self._is_unknown_op_error(err, op):
                return None
            return out
        return out

    def upsert_concurrency_limit(
        self,
        name: str,
        limit: int,
        *,
        slot_decay_per_second: float | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        from . import concurrency_store as gcl

        body: dict[str, Any] = {
            "name": name,
            "limit": limit,
            "active": active,
        }
        if slot_decay_per_second is not None:
            body["slot_decay_per_second"] = slot_decay_per_second
        rust = self._gcl_dispatch("gcl_upsert", body)
        if rust is not None and rust.get("ok") and "limit" in rust:
            return rust["limit"]
        with self._lock:
            return gcl.upsert_limit(self._sqlite_conn, body)

    def delete_concurrency_limit(self, name: str) -> dict[str, Any]:
        from . import concurrency_store as gcl

        rust = self._gcl_dispatch("gcl_delete", {"name": name})
        if rust is not None and "deleted" in rust:
            return rust
        with self._lock:
            return gcl.delete_limit(self._sqlite_conn, name)

    def get_concurrency_limit(self, name: str) -> dict[str, Any] | None:
        from . import concurrency_store as gcl

        rust = self._gcl_dispatch("gcl_get", {"name": name})
        if rust is not None and "limit" in rust:
            lim = rust["limit"]
            return lim if lim is not None else None
        with self._lock:
            return gcl.get_limit(self._sqlite_conn, name).get("limit")

    def list_concurrency_limits(self) -> list[dict[str, Any]]:
        from . import concurrency_store as gcl

        rust = self._gcl_dispatch("gcl_list", {})
        if rust is not None and "limits" in rust:
            return list(rust["limits"] or [])
        with self._lock:
            return list(gcl.list_limits(self._sqlite_conn).get("limits") or [])

    def acquire_concurrency_slots(
        self,
        names: str | list[str],
        *,
        occupy: int = 1,
        mode: str = "concurrency",
        strict: bool = False,
        lease_duration: float = 300.0,
        holder_type: str | None = None,
        holder_id: str | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        from . import concurrency_store as gcl

        body: dict[str, Any] = {
            "names": names,
            "occupy": occupy,
            "mode": mode,
            "strict": strict,
            "lease_duration": lease_duration,
        }
        if holder_type is not None:
            body["holder_type"] = holder_type
        if holder_id is not None:
            body["holder_id"] = holder_id
        if now is not None:
            body["now"] = now
        rust = self._gcl_dispatch("gcl_acquire", body)
        if rust is not None and "status" in rust:
            return rust
        with self._lock:
            return gcl.acquire(self._sqlite_conn, body)

    def release_concurrency_slots(
        self,
        lease_ids: str | list[str],
        *,
        now: str | None = None,
    ) -> dict[str, Any]:
        from . import concurrency_store as gcl

        body: dict[str, Any] = {"lease_ids": lease_ids}
        if now is not None:
            body["now"] = now
        rust = self._gcl_dispatch("gcl_release", body)
        if rust is not None and "released" in rust:
            return rust
        with self._lock:
            return gcl.release(self._sqlite_conn, body)

    def renew_concurrency_slots(
        self,
        lease_ids: str | list[str],
        *,
        lease_duration: float = 300.0,
        now: str | None = None,
    ) -> dict[str, Any]:
        from . import concurrency_store as gcl

        body: dict[str, Any] = {
            "lease_ids": lease_ids,
            "lease_duration": lease_duration,
        }
        if now is not None:
            body["now"] = now
        rust = self._gcl_dispatch("gcl_renew", body)
        if rust is not None and "renewed" in rust:
            return rust
        with self._lock:
            return gcl.renew(self._sqlite_conn, body)

    def reclaim_concurrency_leases(self, *, now: str | None = None) -> int:
        from . import concurrency_store as gcl

        body: dict[str, Any] = {}
        if now is not None:
            body["now"] = now
        rust = self._gcl_dispatch("gcl_reclaim_expired", body)
        if rust is not None and "reclaimed" in rust:
            return int(rust["reclaimed"])
        with self._lock:
            return gcl.reclaim_expired(self._sqlite_conn, now)

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
            "schedule_rrule": d.get("schedule_rrule"),
            "schedule_next_run_at": d.get("schedule_next_run_at"),
            "schedule_enabled": bool(d.get("schedule_enabled")),
            "work_pool_id": d.get("work_pool_id") or DEFAULT_WORK_POOL_ID,
            "created_at": d["created_at"],
            "updated_at": d["updated_at"],
        }

    def start_rust_deployment_scheduler(
        self, interval_ms: int = 1000, stale_after_seconds: int = 120
    ) -> bool:
        bridge = self._rust_fsm_bridge
        handle = self._rust_fsm_handle
        if not bridge or not handle or not self._rust_db_bound:
            return False
        return bool(
            bridge.deployment_scheduler_start(handle, interval_ms, stale_after_seconds)
        )

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

    @staticmethod
    def _parse_rrule_until(value: str) -> datetime:
        raw = value.strip()
        if raw.endswith("Z") and "T" in raw and "-" not in raw:
            raw = raw[:-1]
        try:
            if "-" in raw:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
                    UTC
                )
            return datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        except ValueError as exc:
            raise ValueError(f"invalid RRule UNTIL: {exc}") from exc

    @classmethod
    def _next_rrule_occurrence(cls, expr: str, after: datetime | None = None) -> str:
        parts: dict[str, str] = {}
        for raw_part in expr.split(";"):
            part = raw_part.strip()
            if not part:
                continue
            key, sep, value = part.partition("=")
            if not sep:
                raise ValueError(f"invalid RRule component: {part}")
            normalized_key = key.strip().upper()
            if normalized_key not in {"FREQ", "INTERVAL", "UNTIL", "COUNT"}:
                raise ValueError(f"unsupported RRule component: {normalized_key}")
            parts[normalized_key] = value.strip()

        freq = parts.get("FREQ", "").upper()
        if freq not in {"MINUTELY", "HOURLY", "DAILY", "WEEKLY"}:
            raise ValueError(f"unsupported RRule FREQ: {freq or '<missing>'}")
        if "COUNT" in parts:
            raise ValueError(
                "RRule COUNT is not supported; use UNTIL or trigger fixed runs manually"
            )

        interval = int(parts.get("INTERVAL", "1"))
        if interval <= 0:
            raise ValueError("RRule INTERVAL must be positive")
        unit = {
            "MINUTELY": timedelta(minutes=interval),
            "HOURLY": timedelta(hours=interval),
            "DAILY": timedelta(days=interval),
            "WEEKLY": timedelta(weeks=interval),
        }[freq]
        base = after.astimezone(UTC) if after is not None else datetime.now(UTC)
        nxt = base + unit
        until_raw = parts.get("UNTIL")
        if until_raw:
            until = cls._parse_rrule_until(until_raw)
            if nxt > until:
                raise ValueError("RRule has no upcoming occurrence before UNTIL")
        return nxt.isoformat()

    def deployment_maintenance_tick(
        self, stale_after_seconds: int = 120
    ) -> dict[str, Any]:
        """Reclaim leases, fire due schedules, mark stale workers — prefers a single Rust FFI call."""
        rust = self._rust_deployment_dispatch(
            "deployment_maintenance", {"stale_after_seconds": stale_after_seconds}
        )
        if rust is not None and rust.get("ok"):
            summary = dict(rust.get("summary") or {})
            summary.setdefault("gates_promoted", 0)
            return summary
        with self._lock:
            reclaimed = self._reclaim_expired_claims_python()
            n_tick = self._tick_deployment_schedules_python()
            gates = self._tick_gate_tasks_python()
            now = self._now()
            cutoff = (
                datetime.now(UTC) - timedelta(seconds=max(1, stale_after_seconds))
            ).isoformat()
            cur = self._sqlite_conn.execute(
                "UPDATE workers SET status = 'OFFLINE', updated_at = ? WHERE status = 'ONLINE' AND last_heartbeat < ?",
                [now, cutoff],
            )
            reaped = int(cur.rowcount or 0)
        return {"reclaimed": reclaimed, "triggered": n_tick, "reaped": reaped, "gates_promoted": gates}

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
        due_rrule = self._query_rows(
            """
            SELECT id, schedule_rrule FROM deployments
            WHERE schedule_enabled = 1 AND paused = 0
              AND schedule_rrule IS NOT NULL AND trim(schedule_rrule) != ''
              AND schedule_next_run_at IS NOT NULL AND schedule_next_run_at <= ?
            """,
            [now],
        )
        for row in due_rrule:
            dep_id = UUID(row["id"])
            rrule = str(row["schedule_rrule"])
            try:
                self.trigger_deployment_run(dep_id, parameters={}, idempotency_key=None)
                nxt = self._next_rrule_occurrence(rrule, datetime.now(UTC))
            except ValueError:
                continue
            ts = self._now()
            self._sqlite_conn.execute(
                "UPDATE deployments SET schedule_next_run_at = ?, updated_at = ? WHERE id = ?",
                [nxt, ts, str(dep_id)],
            )
            fired += 1
        return fired

    def worker_heartbeat(
        self, worker_name: str, work_pool_id: str | None = None
    ) -> None:
        pool_id = work_pool_id or os.getenv("IRONFLOW_WORK_POOL", DEFAULT_WORK_POOL_ID)
        rust = self._rust_deployment_dispatch(
            "deployment_worker_heartbeat",
            {"worker_name": worker_name, "work_pool_id": pool_id},
        )
        if rust is not None and rust.get("ok"):
            return
        now = self._now()
        with self._lock:
            self._sqlite_conn.execute(
                """
                INSERT INTO workers(name,last_heartbeat,status,updated_at,work_pool_id)
                VALUES(?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    last_heartbeat=excluded.last_heartbeat,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    work_pool_id=excluded.work_pool_id
                """,
                [worker_name, now, "ONLINE", now, pool_id],
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

    def create_flow_run(
        self,
        name: str,
        *,
        parent_flow_run_id: UUID | None = None,
        parent_task_run_id: UUID | None = None,
        execution_mode: str | None = None,
        resume_from_flow_run_id: UUID | None = None,
        parameters_fingerprint: str | None = None,
    ) -> FlowRunRecord:
        run_id = uuid4()
        root_flow_run_id: UUID | None = run_id
        depth = 0
        if parent_flow_run_id is not None:
            parent = self.get_flow(parent_flow_run_id)
            root_flow_run_id = parent.root_flow_run_id or parent_flow_run_id
            depth = parent.depth + 1
            if depth > SUBFLOW_MAX_DEPTH:
                raise ValueError(f"subflow depth exceeds maximum ({SUBFLOW_MAX_DEPTH})")
        resume_lineage_id: UUID | None = None
        resume_skips_enabled = False
        if resume_from_flow_run_id is not None:
            with self._lock:
                self._ensure_resume_schema()
            prior = self._flows.get(resume_from_flow_run_id)
            prior_fp: str | None = None
            if prior is None:
                # Durable path: prior may only exist in SQLite after process restart.
                prior_row = self._query_rows(
                    "SELECT id, parameters_fingerprint FROM flow_runs WHERE id = ? LIMIT 1",
                    [str(resume_from_flow_run_id)],
                )
                if not prior_row:
                    raise ValueError("resume_from flow run not found")
                resume_lineage_id = self._lineage_id_for_flow(resume_from_flow_run_id)
                raw_fp = prior_row[0]["parameters_fingerprint"]
                prior_fp = str(raw_fp) if raw_fp else None
            else:
                resume_lineage_id = prior.resume_lineage_id or prior.run_id
                prior_fp = prior.parameters_fingerprint
            # Case 6: only skip when flow/deployment params match the prior attempt.
            resume_skips_enabled = (
                prior_fp is not None
                and parameters_fingerprint is not None
                and prior_fp == parameters_fingerprint
            )
            if resume_skips_enabled:
                self._resume_lookups_enabled = True
        record = FlowRunRecord(
            run_id=run_id,
            name=name,
            state=RunState.SCHEDULED,
            version=0,
            parent_flow_run_id=parent_flow_run_id,
            parent_task_run_id=parent_task_run_id,
            root_flow_run_id=root_flow_run_id,
            execution_mode=execution_mode,
            depth=depth,
            resume_from_flow_run_id=resume_from_flow_run_id,
            resume_lineage_id=resume_lineage_id,
            parameters_fingerprint=parameters_fingerprint,
            resume_skips_enabled=resume_skips_enabled,
        )
        with self._lock:
            persisted_by_rust = False
            if self._rust_fsm_active() and self._rust_native_persistence:
                out = self._rust_fsm_call(
                    "create_flow_run_persist",
                    {
                        **(
                            {}
                            if self._rust_db_bound
                            else {"db_path": str(self._sqlite_path)}
                        ),
                        "run": {
                            "id": str(record.run_id),
                            "name": record.name,
                            "state": record.state.value,
                            "version": int(record.version),
                            "parent_flow_run_id": str(parent_flow_run_id)
                            if parent_flow_run_id
                            else None,
                            "parent_task_run_id": str(parent_task_run_id)
                            if parent_task_run_id
                            else None,
                            "root_flow_run_id": str(root_flow_run_id)
                            if root_flow_run_id
                            else None,
                            "execution_mode": execution_mode,
                            "depth": depth,
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
            else:
                # Rust create_flow_run_persist omits resume/param-fingerprint columns.
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
                            str(record.resume_lineage_id)
                            if record.resume_lineage_id
                            else None,
                            record.parameters_fingerprint,
                            str(record.run_id),
                        ],
                    )
            self._persist_record(
                {
                    "record_type": "flow_create",
                    "run_id": str(record.run_id),
                    "name": record.name,
                    "state": record.state.value,
                    "version": record.version,
                    "parent_flow_run_id": str(parent_flow_run_id)
                    if parent_flow_run_id
                    else None,
                    "parent_task_run_id": str(parent_task_run_id)
                    if parent_task_run_id
                    else None,
                    "root_flow_run_id": str(root_flow_run_id)
                    if root_flow_run_id
                    else None,
                    "execution_mode": execution_mode,
                    "depth": depth,
                }
            )
            if (not self._rust_native_persistence) and self._rust_fsm_active():
                self._rust_register_flow(record)
        return record

    def prepare_resume(self, from_flow_run_id: UUID) -> None:
        """Queue resume lineage for the next ``create_flow_run`` (in-process tests / helpers)."""
        with self._lock:
            self._ensure_resume_schema()
            if from_flow_run_id not in self._flows:
                rows = self._query_rows(
                    "SELECT id FROM flow_runs WHERE id = ? LIMIT 1",
                    [str(from_flow_run_id)],
                )
                if not rows:
                    raise ValueError("resume_from flow run not found")
            self._pending_resume_from = from_flow_run_id
            self._resume_lookups_enabled = True

    def consume_pending_resume(self) -> UUID | None:
        with self._lock:
            pending = self._pending_resume_from
            self._pending_resume_from = None
            return pending

    def _lineage_id_for_flow(self, flow_run_id: UUID) -> UUID:
        rec = self._flows.get(flow_run_id)
        if rec is not None:
            return rec.resume_lineage_id or rec.run_id
        rows = self._query_rows(
            "SELECT resume_lineage_id FROM flow_runs WHERE id = ? LIMIT 1",
            [str(flow_run_id)],
        )
        if rows and rows[0]["resume_lineage_id"]:
            return UUID(str(rows[0]["resume_lineage_id"]))
        return flow_run_id

    def effective_resume_lineage_id(self, flow_run_id: UUID) -> UUID | None:
        rec = self._flows.get(flow_run_id)
        if rec is None:
            return None
        if rec.resume_lineage_id is not None:
            return rec.resume_lineage_id
        # Fresh runs use their own id as lineage root when storing results.
        return rec.run_id

    def _ensure_resume_schema(self) -> None:
        """Ensure resume columns / cache table exist (SQLite + Postgres)."""
        if self._resume_schema_ready:
            return
        if getattr(self._store, "backend_kind", "sqlite") == "postgres":
            # Canonical DDL + IF NOT EXISTS upgrades live on PostgresStore.
            self._store.ensure_schema()
            self._task_result_cache_ready = True
            self._resume_schema_ready = True
            return
        flow_cols = {
            c["name"]
            for c in self._sqlite_conn.execute("PRAGMA table_info(flow_runs)").fetchall()
        }
        if "resume_from_flow_run_id" not in flow_cols:
            self._sqlite_conn.execute(
                "ALTER TABLE flow_runs ADD COLUMN resume_from_flow_run_id TEXT"
            )
        if "resume_lineage_id" not in flow_cols:
            self._sqlite_conn.execute(
                "ALTER TABLE flow_runs ADD COLUMN resume_lineage_id TEXT"
            )
        if "parameters_fingerprint" not in flow_cols:
            self._sqlite_conn.execute(
                "ALTER TABLE flow_runs ADD COLUMN parameters_fingerprint TEXT"
            )
        dep_run_cols = {
            c["name"]
            for c in self._sqlite_conn.execute(
                "PRAGMA table_info(deployment_runs)"
            ).fetchall()
        }
        if "resume_from_flow_run_id" not in dep_run_cols:
            self._sqlite_conn.execute(
                "ALTER TABLE deployment_runs ADD COLUMN resume_from_flow_run_id TEXT"
            )
        self._ensure_task_result_cache_table()
        self._resume_schema_ready = True

    def _ensure_task_result_cache_table(self) -> None:
        if self._task_result_cache_ready:
            return
        if getattr(self._store, "backend_kind", "sqlite") == "postgres":
            self._store.ensure_schema()
            self._task_result_cache_ready = True
            return
        self._sqlite_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_result_cache (
                lineage_id TEXT NOT NULL,
                planned_node_id TEXT NOT NULL,
                map_index INTEGER NOT NULL,
                task_name TEXT NOT NULL,
                is_none_result INTEGER NOT NULL,
                has_payload INTEGER NOT NULL,
                payload_json TEXT,
                input_fingerprint TEXT NOT NULL DEFAULT '',
                source_flow_run_id TEXT NOT NULL,
                source_task_run_id TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (lineage_id, planned_node_id, map_index)
            )
            """
        )
        cache_cols = {
            c["name"]
            for c in self._sqlite_conn.execute(
                "PRAGMA table_info(task_result_cache)"
            ).fetchall()
        }
        if "input_fingerprint" not in cache_cols:
            self._sqlite_conn.execute(
                "ALTER TABLE task_result_cache ADD COLUMN input_fingerprint "
                "TEXT NOT NULL DEFAULT ''"
            )
        self._task_result_cache_ready = True

    def lookup_resumed_task_result(
        self,
        flow_run_id: UUID,
        planned_node_id: str | None,
        *,
        map_index: int | None = None,
        persist_result: bool = False,
        input_fingerprint: str | None = None,
    ) -> tuple[bool, Any]:
        """Return ``(hit, value)`` for a DAG node on a resume run."""
        if not self._resume_lookups_enabled:
            return False, None
        if not planned_node_id or not input_fingerprint:
            return False, None
        rec = self._flows.get(flow_run_id)
        if (
            rec is None
            or rec.resume_from_flow_run_id is None
            or not rec.resume_skips_enabled
        ):
            return False, None
        lineage_id = rec.resume_lineage_id or rec.run_id
        map_key = -1 if map_index is None else int(map_index)
        with self._lock:
            self._ensure_resume_schema()
        rows = self._query_rows(
            """
            SELECT is_none_result, has_payload, payload_json, input_fingerprint
            FROM task_result_cache
            WHERE lineage_id = ? AND planned_node_id = ? AND map_index = ?
            LIMIT 1
            """,
            [str(lineage_id), planned_node_id, map_key],
        )
        if not rows:
            return False, None
        row = rows[0]
        if str(row["input_fingerprint"] or "") != input_fingerprint:
            return False, None
        if int(row["is_none_result"] or 0) == 1:
            return True, None
        if int(row["has_payload"] or 0) != 1:
            return False, None
        if not persist_result:
            return False, None
        raw = row["payload_json"]
        if raw is None:
            return False, None
        try:
            return True, decode_task_result(str(raw))
        except Exception:
            return False, None

    def store_task_result_for_resume(
        self,
        flow_run_id: UUID,
        task_run_id: UUID,
        task_name: str,
        planned_node_id: str | None,
        value: Any,
        *,
        persist_result: bool = False,
        map_index: int | None = None,
        input_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """Persist a completion marker / optional JSON payload for DAG resume.

        Returns artifact summary fields (``result`` / ``persisted``) when applicable.
        Cache rows are only written when ``input_fingerprint`` is known (JSON-safe inputs).
        """
        summary_extra: dict[str, Any] = {}
        if not planned_node_id:
            return summary_extra
        is_none = value is None
        if not is_none and not persist_result:
            # Non-persisted value-producing tasks recompute on resume — skip store write.
            return summary_extra
        payload_json: str | None = None
        has_payload = False
        if is_none:
            summary_extra["result"] = None
            summary_extra["persisted"] = True
        else:
            try:
                payload_json = encode_task_result(value)
                has_payload = True
                summary_extra["result"] = decode_task_result(payload_json)
                summary_extra["persisted"] = True
            except ResultEncodeError:
                summary_extra["persisted"] = False
                return summary_extra
        if not input_fingerprint:
            # Unfingerprintable inputs: surface artifact summary but never resume-skip.
            return summary_extra
        lineage_id = self.effective_resume_lineage_id(flow_run_id)
        if lineage_id is None:
            return summary_extra
        map_key = -1 if map_index is None else int(map_index)
        now = self._now()
        with self._lock:
            self._ensure_resume_schema()
            self._sqlite_conn.execute(
                """
                INSERT INTO task_result_cache(
                    lineage_id, planned_node_id, map_index, task_name,
                    is_none_result, has_payload, payload_json, input_fingerprint,
                    source_flow_run_id, source_task_run_id, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(lineage_id, planned_node_id, map_index) DO UPDATE SET
                    task_name=excluded.task_name,
                    is_none_result=excluded.is_none_result,
                    has_payload=excluded.has_payload,
                    payload_json=excluded.payload_json,
                    input_fingerprint=excluded.input_fingerprint,
                    source_flow_run_id=excluded.source_flow_run_id,
                    source_task_run_id=excluded.source_task_run_id,
                    updated_at=excluded.updated_at
                """,
                [
                    str(lineage_id),
                    planned_node_id,
                    map_key,
                    task_name,
                    1 if is_none else 0,
                    1 if has_payload else 0,
                    payload_json,
                    input_fingerprint,
                    str(flow_run_id),
                    str(task_run_id),
                    now,
                ],
            )
            # Fresh runs: keep lineage in memory as run_id; SQL update only for non-identity lineages.
            flow = self._flows.get(flow_run_id)
            if flow is not None and flow.resume_lineage_id is None:
                flow.resume_lineage_id = lineage_id
                if lineage_id != flow_run_id:
                    self._sqlite_conn.execute(
                        "UPDATE flow_runs SET resume_lineage_id = ? WHERE id = ?",
                        [str(lineage_id), str(flow_run_id)],
                    )
        return summary_extra

    def create_task_run(
        self,
        flow_run_id: UUID,
        task_name: str,
        planned_node_id: str | None = None,
        *,
        kind: str = "task",
        child_flow_run_id: UUID | None = None,
        child_deployment_run_id: UUID | None = None,
        gate_open_at: str | None = None,
        tags: Sequence[str] | None = None,
        contribute_to_flow_state: bool = True,
    ) -> TaskRunRecord:
        tag_tuple = tuple(str(t) for t in (tags or ()))
        task = TaskRunRecord(
            task_run_id=uuid4(),
            flow_run_id=flow_run_id,
            task_name=task_name,
            planned_node_id=planned_node_id,
            state=RunState.SCHEDULED,
            version=0,
            kind=kind,
            child_flow_run_id=child_flow_run_id,
            child_deployment_run_id=child_deployment_run_id,
            gate_open_at=gate_open_at,
            tags=tag_tuple,
            contribute_to_flow_state=contribute_to_flow_state,
        )
        with self._lock:
            if self._is_scheduling_held_unlocked(flow_run_id):
                raise FlowRunSchedulingHeld(
                    f"flow run {flow_run_id} is paused; new tasks cannot start until resume"
                )
            self._tasks[task.task_run_id] = task
            persisted_by_rust = False
            if self._rust_fsm_active() and self._rust_native_persistence:
                out = self._rust_fsm_call(
                    "create_task_run_persist",
                    {
                        **(
                            {}
                            if self._rust_db_bound
                            else {"db_path": str(self._sqlite_path)}
                        ),
                        "planned_node_id": task.planned_node_id,
                        "kind": task.kind,
                        "child_flow_run_id": str(child_flow_run_id)
                        if child_flow_run_id
                        else None,
                        "child_deployment_run_id": str(child_deployment_run_id)
                        if child_deployment_run_id
                        else None,
                        "gate_open_at": gate_open_at,
                        "contribute_to_flow_state": task.contribute_to_flow_state,
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
                    "tags": list(task.tags),
                    "contribute_to_flow_state": task.contribute_to_flow_state,
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
                        **(
                            {}
                            if self._rust_db_bound
                            else {"db_path": str(self._sqlite_path)}
                        ),
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
            reserved = self._reserved_planned_ids.setdefault(flow_run_id, set())
            task_run_used = {
                str(task.planned_node_id)
                for task in self._tasks.values()
                if task.flow_run_id == flow_run_id and task.planned_node_id
            }
            taken = reserved | task_run_used

            by_task = self._manifest_by_task.get(flow_run_id)
            if by_task is not None:
                for node_id in by_task.get(task_name, []):
                    if node_id not in taken:
                        reserved.add(node_id)
                        return node_id
            else:
                manifest_id = self._next_planned_from_sql_unlocked(
                    flow_run_id, task_name
                )
                if manifest_id is not None and manifest_id not in taken:
                    reserved.add(manifest_id)
                    return manifest_id

            index = 0
            while True:
                candidate = f"dyn_{task_name}_{index}"
                if candidate not in taken:
                    reserved.add(candidate)
                    return candidate
                index += 1

    def _next_planned_from_sql_unlocked(
        self, flow_run_id: UUID, task_name: str
    ) -> str | None:
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
                op = (
                    "set_flow_state_persist" if use_native_persist else "set_flow_state"
                )
                payload: dict[str, Any] = (
                    self._persist_payload(body) if use_native_persist else body
                )
                out = self._rust_fsm_call(op, payload)
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if use_native_persist and self._is_unknown_op_error(
                        err, "set_flow_state_persist"
                    ):
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
                    return SetStateResult(
                        status="duplicate", state=new_state, version=new_version
                    )

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
                return SetStateResult(
                    status="applied", state=record.state, version=record.version
                )

            if transition_token in self._tokens:
                return SetStateResult(
                    status="duplicate", state=record.state, version=record.version
                )

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
            return SetStateResult(
                status="applied", state=record.state, version=record.version
            )

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
                results.append(
                    self.set_flow_state(run_id, to_state, token, kind, expected_version)
                )
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
                        **(
                            {}
                            if self._rust_db_bound
                            else {"db_path": str(self._sqlite_path)}
                        ),
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
                results.append(
                    self.set_flow_state(run_id, to_state, token, kind, expected_version)
                )
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
                synthesized.append(
                    SetStateResult(
                        status="duplicate", state=new_state, version=new_version
                    )
                )
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
            synthesized.append(
                SetStateResult(status="applied", state=new_state, version=new_version)
            )
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

            fenced_late_event = (
                task.state == RunState.CANCELLED
                and event_type
                in {
                    "task_completed",
                    "task_failed",
                    "task_running",
                    "task_pending",
                }
            )
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

    def list_flow_runs(
        self, state: str | None = None, limit: int = 50, cursor: str | None = None
    ) -> PageResult:
        rust_result = self._query_rust(
            "flow_runs", {"state": state, "limit": limit, "cursor": cursor}
        )
        if rust_result is not None:
            return PageResult(
                items=rust_result["items"], next_cursor=rust_result["next_cursor"]
            )
        query = (
            "SELECT seq,id,name,state,version,created_at,updated_at,parent_flow_run_id,parent_task_run_id,"
            "root_flow_run_id,execution_mode,depth FROM flow_runs"
        )
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
        rust_result = self._query_rust(
            "flow_run_detail", {"flow_run_id": str(flow_run_id)}
        )
        if rust_result is not None:
            result = rust_result
        else:
            rows = self._query_rows(
                "SELECT seq,id,name,state,version,created_at,updated_at,parent_flow_run_id,parent_task_run_id,"
                "root_flow_run_id,execution_mode,depth FROM flow_runs WHERE id = ? LIMIT 1",
                [str(flow_run_id)],
            )
            if not rows:
                return None
            result = self._flow_row_to_dict(rows[0])
        dep_rows = self._query_rows(
            "SELECT deployment_id FROM deployment_runs WHERE flow_run_id = ? ORDER BY created_at DESC LIMIT 1",
            [str(flow_run_id)],
        )
        if dep_rows:
            result["deployment_id"] = dep_rows[0]["deployment_id"]
        result["breadcrumb"] = self._flow_run_breadcrumb(flow_run_id)
        result["children_summary"] = self._flow_run_children_summary(flow_run_id)
        result["children"] = self._flow_run_children(flow_run_id)
        life = self._lifecycle_by_flow.get(str(flow_run_id))
        if life:
            result["lifecycle_action"] = life.get("lifecycle_action")
            result["interrupt_mode"] = life.get("interrupt_mode")
            if life.get("pause_drain_pending"):
                result["pause_drain_pending"] = True
            summary = life.get("lifecycle_summary")
            if summary:
                result["lifecycle_summary"] = summary
        else:
            result.setdefault("lifecycle_action", None)
            result.setdefault("interrupt_mode", None)
        return result

    def list_task_runs(
        self, flow_run_id: UUID, limit: int = 200, cursor: str | None = None
    ) -> PageResult:
        rust_result = self._query_rust(
            "task_runs",
            {"flow_run_id": str(flow_run_id), "limit": limit, "cursor": cursor},
        )
        if rust_result is not None:
            return PageResult(
                items=rust_result["items"], next_cursor=rust_result["next_cursor"]
            )
        query = (
            "SELECT seq,id,flow_run_id,task_name,planned_node_id,state,version,created_at,updated_at,"
            "kind,child_flow_run_id,child_deployment_run_id "
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
            return PageResult(
                items=rust_result["items"], next_cursor=rust_result["next_cursor"]
            )
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
            return PageResult(
                items=rust_result["items"], next_cursor=rust_result["next_cursor"]
            )
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

    def list_tasks(
        self, flow_name: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        rust_result = self._query_rust(
            "tasks", {"flow_name": flow_name, "limit": limit}
        )
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
            {
                "task_name": row["task_name"],
                "run_count": row["run_count"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def list_events(
        self, flow_run_id: UUID, limit: int = 500, cursor: str | None = None
    ) -> PageResult:
        rust_result = self._query_rust(
            "events",
            {"flow_run_id": str(flow_run_id), "limit": limit, "cursor": cursor},
        )
        if rust_result is not None:
            return PageResult(
                items=rust_result["items"], next_cursor=rust_result["next_cursor"]
            )
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

    def list_artifacts_for_flow(
        self, flow_run_id: UUID, limit: int = 200
    ) -> list[dict[str, Any]]:
        rust_result = self._query_rust(
            "artifacts_flow", {"flow_run_id": str(flow_run_id), "limit": limit}
        )
        if rust_result is not None:
            return rust_result
        rows = self._query_rows(
            "SELECT id,flow_run_id,task_run_id,artifact_type,key,summary,created_at "
            "FROM artifacts WHERE flow_run_id = ? ORDER BY created_at DESC LIMIT ?",
            [str(flow_run_id), limit],
        )
        return [self._artifact_row_to_dict(r) for r in rows]

    def list_artifacts_for_task(
        self, task_run_id: UUID, limit: int = 200
    ) -> list[dict[str, Any]]:
        rust_result = self._query_rust(
            "artifacts_task", {"task_run_id": str(task_run_id), "limit": limit}
        )
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
        schedule_rrule: str | None = None,
        schedule_next_run_at: str | None = None,
        schedule_enabled: bool = False,
        work_pool_id: str | None = None,
    ) -> dict[str, Any]:
        pool_id = work_pool_id or DEFAULT_WORK_POOL_ID
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
            "schedule_rrule": schedule_rrule,
            "schedule_next_run_at": schedule_next_run_at,
            "schedule_enabled": schedule_enabled,
            "work_pool_id": pool_id,
        }
        rrule_requested = bool(schedule_rrule and str(schedule_rrule).strip())
        rust = (
            None
            if rrule_requested
            else self._rust_deployment_dispatch("deployment_create", body)
        )
        if rust is not None and rust.get("ok") and rust.get("deployment") is not None:
            deployment = self._deployment_from_rust_json(rust["deployment"])
            return deployment
        if rust is not None and rust.get("ok") is False:
            err = rust.get("error") or {}
            raise ValueError(str(err.get("message", "deployment_create failed")))

        with self._lock:
            existing = self._query_rows(
                """
                SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                       concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,
                       schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at
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
            sr = schedule_rrule
            if sr and str(sr).strip():
                si = None
                sc = None
            elif sc and str(sc).strip():
                si = None
                sr = None
            elif si is not None and si > 0:
                sc = None
                sr = None

            sched_next = schedule_next_run_at
            if schedule_enabled and si and si > 0 and sched_next is None:
                sched_next = self._now()
            if schedule_enabled and sr and str(sr).strip() and sched_next is None:
                sched_next = self._next_rrule_occurrence(str(sr), datetime.now(UTC))
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
                 concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,
                 schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    sr,
                    sched_next,
                    1 if schedule_enabled else 0,
                    pool_id,
                    now,
                    now,
                ],
            )
            row = self._query_rows(
                """
                SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                       concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,
                       schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at
                FROM deployments
                WHERE id = ?
                LIMIT 1
                """,
                [deployment_id],
            )[0]
            return self._deployment_row_to_dict(row)

    def update_deployment(
        self, deployment_id: UUID, patch: dict[str, Any]
    ) -> dict[str, Any]:
        body = dict(patch)
        body["deployment_id"] = str(deployment_id)
        rrule_requested = bool(
            patch.get("schedule_rrule") and str(patch.get("schedule_rrule")).strip()
        )
        rust = (
            None
            if rrule_requested
            else self._rust_deployment_dispatch("deployment_update", body)
        )
        if rust is not None and rust.get("ok") and rust.get("deployment") is not None:
            deployment = self._deployment_from_rust_json(rust["deployment"])
            return deployment
        if rust is not None and rust.get("ok") is False:
            err = rust.get("error") or {}
            msg = str(err.get("message", "deployment_update failed"))
            if err.get("code") == "not_found":
                raise ValueError("deployment not found")
            raise ValueError(msg)

        return self._update_deployment_python(deployment_id, patch)

    def _update_deployment_python(
        self, deployment_id: UUID, patch: dict[str, Any]
    ) -> dict[str, Any]:
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
            schedule_rrule = dep.get("schedule_rrule")
            schedule_next_run_at = dep.get("schedule_next_run_at")
            schedule_enabled = bool(dep.get("schedule_enabled"))
            work_pool_id = dep.get("work_pool_id") or DEFAULT_WORK_POOL_ID

            if "entrypoint" in patch:
                v = patch["entrypoint"]
                entrypoint = None if v is None else str(v)
            if "path" in patch:
                v = patch["path"]
                path = None if v is None else str(v)
            if (
                "default_parameters" in patch
                and patch["default_parameters"] is not None
            ):
                default_parameters = dict(patch["default_parameters"])
            if "paused" in patch:
                paused = bool(patch["paused"])
            if "concurrency_limit" in patch:
                concurrency_limit = patch["concurrency_limit"]
            if (
                "collision_strategy" in patch
                and patch["collision_strategy"] is not None
            ):
                collision_strategy = str(patch["collision_strategy"])
            if "schedule_interval_seconds" in patch:
                schedule_interval_seconds = patch["schedule_interval_seconds"]
            if "schedule_cron" in patch:
                schedule_cron = patch["schedule_cron"]
            if "schedule_rrule" in patch:
                schedule_rrule = patch["schedule_rrule"]
            if "schedule_next_run_at" in patch:
                schedule_next_run_at = patch["schedule_next_run_at"]
            if "schedule_enabled" in patch:
                schedule_enabled = bool(patch["schedule_enabled"])
            if "work_pool_id" in patch and patch["work_pool_id"] is not None:
                work_pool_id = str(patch["work_pool_id"])

            if schedule_rrule and str(schedule_rrule).strip():
                schedule_interval_seconds = None
                schedule_cron = None
            elif schedule_cron and str(schedule_cron).strip():
                schedule_interval_seconds = None
                schedule_rrule = None
            elif (
                schedule_interval_seconds is not None
                and int(schedule_interval_seconds) > 0
            ):
                schedule_cron = None
                schedule_rrule = None

            if (
                schedule_enabled
                and schedule_interval_seconds
                and int(schedule_interval_seconds) > 0
                and not schedule_next_run_at
            ):
                schedule_next_run_at = self._now()
            if (
                schedule_enabled
                and schedule_rrule
                and str(schedule_rrule).strip()
                and not schedule_next_run_at
            ):
                schedule_next_run_at = self._next_rrule_occurrence(
                    str(schedule_rrule), datetime.now(UTC)
                )
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
                  schedule_interval_seconds = ?, schedule_cron = ?, schedule_rrule = ?, schedule_next_run_at = ?,
                  schedule_enabled = ?, work_pool_id = ?, updated_at = ?
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
                    schedule_rrule,
                    schedule_next_run_at,
                    1 if schedule_enabled else 0,
                    work_pool_id,
                    ts,
                    str(deployment_id),
                ],
            )
            row = self._query_rows(
                """
                SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                       concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,
                       schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at
                FROM deployments WHERE id = ?
                LIMIT 1
                """,
                [str(deployment_id)],
            )[0]
            return self._deployment_row_to_dict(row)

    def list_deployments(
        self, limit: int = 200, cursor: str | None = None
    ) -> PageResult:
        query = (
            "SELECT seq,id,name,flow_name,entrypoint,path,default_parameters,paused,"
            " concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,"
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
                   concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,
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

    def get_deployment_by_name(self, name: str) -> dict[str, Any] | None:
        rows = self._query_rows(
            """
            SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                   concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,
                   schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at
            FROM deployments
            WHERE name = ?
            LIMIT 1
            """,
            [name],
        )
        if not rows:
            return None
        return self._deployment_row_to_dict(rows[0])

    def set_flow_result(self, run_id: UUID, result: Any) -> None:
        with self._lock:
            self._flow_results[run_id] = result

    def get_flow_result(self, run_id: UUID) -> Any:
        with self._lock:
            return self._flow_results.get(run_id)

    def _merge_resume_from_flow_run_id(
        self, run: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Attach SQLite ``resume_from_flow_run_id`` when Rust ops omit the column."""
        if run is None or run.get("resume_from_flow_run_id"):
            return run
        run_id = run.get("id")
        if not run_id:
            return run
        with self._lock:
            self._ensure_resume_schema()
        rows = self._query_rows(
            "SELECT resume_from_flow_run_id FROM deployment_runs WHERE id = ? LIMIT 1",
            [str(run_id)],
        )
        if not rows:
            return run
        merged = dict(run)
        merged["resume_from_flow_run_id"] = rows[0]["resume_from_flow_run_id"]
        return merged

    def get_deployment_run(self, deployment_run_id: UUID) -> dict[str, Any] | None:
        rust = self._rust_deployment_dispatch(
            "deployment_get_run", {"deployment_run_id": str(deployment_run_id)}
        )
        if rust is not None:
            if rust.get("ok"):
                run = rust.get("run")
                if run is not None:
                    return self._merge_resume_from_flow_run_id(run)
            else:
                err = rust.get("error") or {}
                raise RuntimeError(str(err.get("message", "deployment get run failed")))
        with self._lock:
            self._ensure_resume_schema()
        rows = self._query_rows(
            """
            SELECT seq,id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
                   worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,
                   resume_from_flow_run_id,
                   created_at,updated_at,started_at,finished_at
            FROM deployment_runs
            WHERE id = ?
            LIMIT 1
            """,
            [str(deployment_run_id)],
        )
        if not rows:
            return None
        return self._deployment_run_row_to_dict(rows[0])

    _DEPLOYMENT_TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
    _DEPLOYMENT_ACTIVE = frozenset({"SCHEDULED", "CLAIMED", "RUNNING"})

    def update_subflow_task_linkage(
        self,
        task_run_id: UUID,
        *,
        child_flow_run_id: UUID | None = None,
        child_deployment_run_id: UUID | None = None,
    ) -> None:
        if child_flow_run_id is None and child_deployment_run_id is None:
            return
        changed = False
        with self._lock:
            task = self._tasks.get(task_run_id)
            if task is None:
                return
            updates: list[str] = []
            params: list[str] = []
            if (
                child_flow_run_id is not None
                and task.child_flow_run_id != child_flow_run_id
            ):
                task.child_flow_run_id = child_flow_run_id
                updates.append("child_flow_run_id = ?")
                params.append(str(child_flow_run_id))
                changed = True
            if (
                child_deployment_run_id is not None
                and task.child_deployment_run_id != child_deployment_run_id
            ):
                task.child_deployment_run_id = child_deployment_run_id
                updates.append("child_deployment_run_id = ?")
                params.append(str(child_deployment_run_id))
                changed = True
            if updates:
                params.append(str(task_run_id))
                self._sqlite_conn.execute(
                    f"UPDATE task_runs SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
        if changed:
            self._persist_task_subflow_linkage(
                task_run_id, child_flow_run_id, child_deployment_run_id
            )

    def _persist_task_subflow_linkage(
        self,
        task_run_id: UUID,
        child_flow_run_id: UUID | None,
        child_deployment_run_id: UUID | None,
    ) -> None:
        self._persist_record(
            {
                "record_type": "task_subflow_linkage",
                "task_run_id": str(task_run_id),
                "child_flow_run_id": str(child_flow_run_id)
                if child_flow_run_id
                else None,
                "child_deployment_run_id": str(child_deployment_run_id)
                if child_deployment_run_id
                else None,
            }
        )

    def update_subflow_task_child_flow_run(
        self, task_run_id: UUID, child_flow_run_id: UUID
    ) -> None:
        self.update_subflow_task_linkage(
            task_run_id, child_flow_run_id=child_flow_run_id
        )

    def mirror_subflow_task_from_deployment(
        self, task_run_id: UUID, deployment_run: dict[str, Any]
    ) -> None:
        status = str(deployment_run.get("status", ""))
        dep_run_id = deployment_run.get("id")
        child_flow_run_id = deployment_run.get("flow_run_id")
        linkage_kwargs: dict[str, UUID] = {}
        if dep_run_id:
            linkage_kwargs["child_deployment_run_id"] = UUID(str(dep_run_id))
        if child_flow_run_id:
            linkage_kwargs["child_flow_run_id"] = UUID(str(child_flow_run_id))
        if linkage_kwargs:
            self.update_subflow_task_linkage(task_run_id, **linkage_kwargs)

        task = self.get_task_run(task_run_id)
        if status in {"SCHEDULED", "CLAIMED"} and task.state == RunState.SCHEDULED:
            self.record_task_event(task_run_id, "task_pending", {"subflow": True})
        elif status == "RUNNING" and task.state in {
            RunState.SCHEDULED,
            RunState.PENDING,
        }:
            if task.state == RunState.SCHEDULED:
                self.record_task_event(task_run_id, "task_pending", {"subflow": True})
            self.record_task_event(task_run_id, "task_running", {"subflow": True})
        elif status == "COMPLETED" and task.state not in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            if task.state == RunState.SCHEDULED:
                self.record_task_event(task_run_id, "task_pending", {"subflow": True})
            if task.state in {RunState.SCHEDULED, RunState.PENDING}:
                self.record_task_event(task_run_id, "task_running", {"subflow": True})
            self.record_task_event(task_run_id, "task_completed", {"subflow": True})
        elif status == "FAILED" and task.state not in {
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            if task.state == RunState.SCHEDULED:
                self.record_task_event(task_run_id, "task_pending", {"subflow": True})
            if task.state in {RunState.SCHEDULED, RunState.PENDING}:
                self.record_task_event(task_run_id, "task_running", {"subflow": True})
            err = deployment_run.get("error") or "subflow deployment failed"
            self.record_task_event(
                task_run_id, "task_failed", {"subflow": True, "error": str(err)}
            )
        elif status == "CANCELLED" and task.state not in {
            RunState.CANCELLED,
            RunState.FAILED,
            RunState.COMPLETED,
        }:
            if task.state == RunState.SCHEDULED:
                self.record_task_event(task_run_id, "task_pending", {"subflow": True})
            if task.state in {RunState.SCHEDULED, RunState.PENDING}:
                self.record_task_event(task_run_id, "task_running", {"subflow": True})
            self.record_task_event(
                task_run_id,
                "task_cancelled",
                {"subflow": True, "error": deployment_run.get("error") or "cancelled"},
            )

    def wait_for_deployment_run_terminal(
        self,
        deployment_run_id: UUID,
        *,
        parent_task_run_id: UUID | None = None,
        timeout_seconds: float = 3600.0,
        poll_seconds: float = 0.05,
    ) -> dict[str, Any]:
        from .cancellation import FlowRunCancelled, assert_flow_not_cancelled
        from .decorators import _ACTIVE_FLOW_RUN

        deadline = time.monotonic() + max(0.0, timeout_seconds)
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            parent_flow_id = _ACTIVE_FLOW_RUN.get()
            if parent_flow_id is not None:
                try:
                    assert_flow_not_cancelled(parent_flow_id)
                except FlowRunCancelled:
                    raise
            last = self.get_deployment_run(deployment_run_id)
            if last is None:
                if time.monotonic() + poll_seconds < deadline:
                    time.sleep(poll_seconds)
                    continue
                raise ValueError(f"deployment run not found: {deployment_run_id}")
            if parent_task_run_id is not None:
                self.mirror_subflow_task_from_deployment(parent_task_run_id, last)
            status = str(last.get("status", ""))
            if status in self._DEPLOYMENT_TERMINAL:
                if parent_task_run_id is not None:
                    self.mirror_subflow_task_from_deployment(parent_task_run_id, last)
                return last
            time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        raise TimeoutError(f"timed out waiting for deployment run {deployment_run_id}")

    _CHILD_TERMINAL = frozenset(
        {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
    )

    def list_contributing_children(self, flow_run_id: UUID) -> list[dict[str, Any]]:
        """Return contributing child task rows (Rust when bound; else SQLite)."""
        if self._rust_fsm_active() and self._rust_db_bound:
            out = self._rust_fsm_call(
                "list_contributing_children",
                {"flow_run_id": str(flow_run_id)},
            )
            if out.get("ok", True) and "items" in out:
                return list(out.get("items") or [])
            err = out.get("error", {})
            if not self._is_unknown_op_error(err, "list_contributing_children"):
                self._raise_from_rust_fsm_error(err)
        return self._list_contributing_children_python(flow_run_id)

    def resolve_flow_terminal_state(self, flow_run_id: UUID) -> dict[str, Any]:
        """Aggregate contributing child states → flow terminal state (Rust hot path)."""
        if self._rust_fsm_active() and self._rust_db_bound:
            out = self._rust_fsm_call(
                "resolve_flow_terminal_state",
                {"flow_run_id": str(flow_run_id)},
            )
            if out.get("ok", True) and "state" in out:
                return out
            err = out.get("error", {})
            if not self._is_unknown_op_error(err, "resolve_flow_terminal_state"):
                self._raise_from_rust_fsm_error(err)
        return self._resolve_flow_terminal_state_python(flow_run_id)

    def wait_contributing_children(
        self,
        flow_run_id: UUID,
        *,
        timeout_seconds: float = 3600.0,
        poll_seconds: float = 0.05,
    ) -> None:
        """Block until all contributing children are terminal (waits deployment subflows)."""
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while time.monotonic() < deadline:
            items = self.list_contributing_children(flow_run_id)
            open_items = [
                item
                for item in items
                if str(item.get("state", "")) not in {
                    s.value for s in self._CHILD_TERMINAL
                }
            ]
            if not open_items:
                return
            waited = False
            for item in open_items:
                dep = item.get("child_deployment_run_id")
                if dep:
                    remaining = max(0.0, deadline - time.monotonic())
                    self.wait_for_deployment_run_terminal(
                        UUID(str(dep)),
                        parent_task_run_id=UUID(str(item["id"])),
                        timeout_seconds=remaining,
                        poll_seconds=poll_seconds,
                    )
                    waited = True
            if not waited:
                # Promote due temporal gates so wait_all does not hang forever on
                # after=0 (or past-until) gates that never got GateFuture.result().
                try:
                    self.tick_gate_tasks()
                except Exception:
                    pass
                time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        raise TimeoutError(
            f"timed out waiting for contributing children of flow run {flow_run_id}"
        )

    def _list_contributing_children_python(
        self, flow_run_id: UUID
    ) -> list[dict[str, Any]]:
        rows = self._query_rows(
            """
            SELECT id, task_name, state, COALESCE(kind, 'task') AS kind,
                   child_deployment_run_id,
                   COALESCE(contribute_to_flow_state, 1) AS contribute_to_flow_state
            FROM task_runs
            WHERE flow_run_id = ?
              AND COALESCE(contribute_to_flow_state, 1) != 0
            ORDER BY seq ASC
            """,
            [str(flow_run_id)],
        )
        return [
            {
                "id": row["id"],
                "task_name": row["task_name"],
                "state": row["state"],
                "kind": row["kind"],
                "child_deployment_run_id": row["child_deployment_run_id"],
            }
            for row in rows
        ]

    def _resolve_flow_terminal_state_python(
        self, flow_run_id: UUID
    ) -> dict[str, Any]:
        items = self._list_contributing_children_python(flow_run_id)
        counts = {
            "total": len(items),
            "COMPLETED": 0,
            "FAILED": 0,
            "CANCELLED": 0,
            "non_terminal": 0,
            "other": 0,
        }
        sample_failures: list[dict[str, Any]] = []
        sample_cancelled: list[dict[str, Any]] = []
        sample_incomplete: list[dict[str, Any]] = []
        for item in items:
            st = str(item.get("state", ""))
            sample = {
                "id": item.get("id"),
                "task_name": item.get("task_name"),
                "state": st,
                "kind": item.get("kind"),
                "child_deployment_run_id": item.get("child_deployment_run_id"),
            }
            if st == "COMPLETED":
                counts["COMPLETED"] += 1
            elif st == "FAILED":
                counts["FAILED"] += 1
                if len(sample_failures) < 8:
                    sample_failures.append(sample)
            elif st == "CANCELLED":
                counts["CANCELLED"] += 1
                if len(sample_cancelled) < 8:
                    sample_cancelled.append(sample)
            elif st in {"SCHEDULED", "PENDING", "RUNNING", "PAUSED", "CANCELLING"}:
                counts["non_terminal"] += 1
                if len(sample_incomplete) < 8:
                    sample_incomplete.append(sample)
            else:
                counts["other"] += 1
                counts["FAILED"] += 1
                if len(sample_failures) < 8:
                    sample_failures.append(sample)
        if not items:
            kind = "empty"
            state = "COMPLETED"
        elif counts["CANCELLED"] > 0:
            kind = "child_cancelled"
            state = "CANCELLED"
        elif counts["FAILED"] > 0:
            kind = "child_failed"
            state = "FAILED"
        elif counts["non_terminal"] > 0:
            kind = "incomplete_children"
            state = "FAILED"
        else:
            kind = "all_completed"
            state = "COMPLETED"
        return {
            "ok": True,
            "state": state,
            "kind": kind,
            "counts": counts,
            "sample_failures": sample_failures,
            "sample_cancelled": sample_cancelled,
            "sample_incomplete": sample_incomplete,
            "_via": "python",
        }

    def pause_flow_for_gate(self, flow_run_id: UUID, gate_task_run_id: UUID) -> None:
        flow = self.get_flow(flow_run_id)
        if flow.state == RunState.RUNNING:
            try:
                self.set_flow_state(
                    flow_run_id, RunState.PAUSED, uuid4(), "gate_wait"
                )
            except ValueError:
                pass
        self._persist_record(
            {
                "record_type": "gate_wait",
                "flow_run_id": str(flow_run_id),
                "gate_task_run_id": str(gate_task_run_id),
            }
        )

    def resume_flow_from_gate(self, flow_run_id: UUID) -> None:
        flow = self.get_flow(flow_run_id)
        if flow.state == RunState.PAUSED:
            try:
                self.set_flow_state(
                    flow_run_id, RunState.RUNNING, uuid4(), "gate_open"
                )
            except ValueError:
                pass

    def complete_gate_task(self, task_run_id: UUID) -> None:
        task = self.get_task_run(task_run_id)
        if task.state == RunState.COMPLETED:
            return
        if task.state == RunState.CANCELLED:
            return
        if task.state == RunState.SCHEDULED:
            self.record_task_event(task_run_id, "task_pending", {"gate": True})
        if self.get_task_run(task_run_id).state in {
            RunState.SCHEDULED,
            RunState.PENDING,
        }:
            self.record_task_event(
                task_run_id, "task_running", {"gate": True, "opened_at": self._now()}
            )
            self.record_task_event(
                task_run_id,
                "task_completed",
                {"gate": True, "opened_at": self._now()},
            )

    def cancel_gate_task(self, task_run_id: UUID) -> None:
        task = self.get_task_run(task_run_id)
        if task.state in {RunState.COMPLETED, RunState.CANCELLED, RunState.FAILED}:
            return
        if task.state == RunState.SCHEDULED:
            self.record_task_event(task_run_id, "task_pending", {"gate": True})
        if self.get_task_run(task_run_id).state in {
            RunState.SCHEDULED,
            RunState.PENDING,
        }:
            self.record_task_event(
                task_run_id,
                "task_running",
                {"gate": True},
            )
            self.record_task_event(
                task_run_id,
                "task_cancelled",
                {"gate": True, "error": "parent flow cancelled"},
            )

    def fail_gate_task(self, task_run_id: UUID, error: str) -> None:
        task = self.get_task_run(task_run_id)
        if task.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            return
        if task.state == RunState.SCHEDULED:
            self.record_task_event(task_run_id, "task_pending", {"gate": True})
        if self.get_task_run(task_run_id).state in {
            RunState.SCHEDULED,
            RunState.PENDING,
            RunState.RUNNING,
        }:
            self.record_task_event(
                task_run_id,
                "task_failed",
                {"gate": True, "error": error},
            )

    def tick_gate_tasks(self) -> int:
        """Promote due gate tasks (PENDING → COMPLETED). Prefers Rust hot path when bound."""
        rust = self._rust_deployment_dispatch("task_tick_gate_tasks", {})
        if rust is not None and rust.get("ok"):
            promoted = int(rust.get("promoted", 0))
            if promoted:
                self._sync_gate_tasks_from_sqlite()
                return promoted
        return self._tick_gate_tasks_python()

    def _tick_gate_tasks_python(self) -> int:
        now = self._now()
        now_dt = datetime.now(UTC)
        due_ids: list[UUID] = []
        rows = self._query_rows(
            """
            SELECT id FROM task_runs
            WHERE kind = 'gate' AND state = 'PENDING'
              AND gate_open_at IS NOT NULL AND gate_open_at <= ?
            """,
            [now],
        )
        due_ids.extend(UUID(str(row["id"])) for row in rows)
        with self._lock:
            for task in self._tasks.values():
                if task.kind != "gate" or task.state != RunState.PENDING:
                    continue
                if not task.gate_open_at:
                    continue
                open_raw = task.gate_open_at.replace("Z", "+00:00")
                open_at = datetime.fromisoformat(open_raw)
                if open_at.tzinfo is None:
                    open_at = open_at.replace(tzinfo=UTC)
                if open_at <= now_dt and task.task_run_id not in due_ids:
                    due_ids.append(task.task_run_id)
        for task_id in due_ids:
            self.complete_gate_task(task_id)
        return len(due_ids)

    def _sync_gate_tasks_from_sqlite(self) -> None:
        """Refresh in-memory gate task states after Rust promotion tick."""
        rows = self._query_rows(
            "SELECT id, state, version FROM task_runs WHERE kind = 'gate'",
            [],
        )
        for row in rows:
            tid = UUID(str(row["id"]))
            task = self._tasks.get(tid)
            if task is None:
                continue
            try:
                task.state = RunState(str(row["state"]))
                task.version = int(row["version"])
            except ValueError:
                continue

    def trigger_deployment_run(
        self,
        deployment_id: UUID,
        parameters: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        *,
        parent_flow_run_id: UUID | None = None,
        parent_task_run_id: UUID | None = None,
        parent_deployment_run_id: UUID | None = None,
        resume_from_flow_run_id: UUID | None = None,
    ) -> dict[str, Any]:
        rust_body: dict[str, Any] = {
            "deployment_id": str(deployment_id),
            "parameters": parameters,
            "idempotency_key": idempotency_key,
        }
        if parent_flow_run_id is not None:
            rust_body["parent_flow_run_id"] = str(parent_flow_run_id)
        if parent_task_run_id is not None:
            rust_body["parent_task_run_id"] = str(parent_task_run_id)
        if parent_deployment_run_id is not None:
            rust_body["parent_deployment_run_id"] = str(parent_deployment_run_id)
        rust = self._rust_deployment_dispatch("deployment_trigger_run", rust_body)
        if rust is not None:
            if rust.get("ok"):
                run = rust["run"]
                if resume_from_flow_run_id is not None:
                    run_id = run.get("id") if isinstance(run, dict) else None
                    if not run_id:
                        raise ValueError(
                            "deployment trigger succeeded but returned no run id; "
                            "cannot attach resume_from_flow_run_id"
                        )
                    with self._lock:
                        self._ensure_resume_schema()
                        self._sqlite_conn.execute(
                            "UPDATE deployment_runs SET resume_from_flow_run_id = ? WHERE id = ?",
                            [str(resume_from_flow_run_id), str(run_id)],
                        )
                    run = dict(run)
                    run["resume_from_flow_run_id"] = str(resume_from_flow_run_id)
                return run
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
                           worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,
                           created_at,updated_at,started_at,finished_at
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
            if (
                limit is not None
                and strategy == "CANCEL_NEW"
                and self._count_exec_runs(str(deployment_id)) >= int(limit)
            ):
                status = "CANCELLED"
                err_msg = "concurrency limit reached"
            now = self._now()
            run_id = str(uuid4())
            # INSERT always names resume_from_flow_run_id; ensure column exists even
            # when resume is unset (common deployment / subflow trigger path).
            self._ensure_resume_schema()
            self._sqlite_conn.execute(
                """
                INSERT INTO deployment_runs
                (id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
                 worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,
                 resume_from_flow_run_id,
                 created_at,updated_at,started_at,finished_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    str(parent_flow_run_id) if parent_flow_run_id else None,
                    str(parent_task_run_id) if parent_task_run_id else None,
                    str(parent_deployment_run_id) if parent_deployment_run_id else None,
                    str(resume_from_flow_run_id) if resume_from_flow_run_id else None,
                    now,
                    now,
                    None,
                    None,
                ],
            )
            row = self._query_rows(
                """
                SELECT seq,id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
                       worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,
                       resume_from_flow_run_id,
                       created_at,updated_at,started_at,finished_at
                FROM deployment_runs
                WHERE id = ?
                LIMIT 1
                """,
                [run_id],
            )[0]
            return self._deployment_run_row_to_dict(row)

    def list_deployment_runs(
        self,
        deployment_id: UUID | None = None,
        limit: int = 200,
        cursor: str | None = None,
    ) -> PageResult:
        with self._lock:
            self._ensure_resume_schema()
        query = (
            "SELECT seq,id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,"
            " worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,"
            " resume_from_flow_run_id,"
            " created_at,updated_at,started_at,finished_at "
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

    def _cancel_deployment_runs_for_parent_flow(
        self, parent_flow_run_id: UUID
    ) -> list[dict[str, Any]]:
        """Cancel SCHEDULED/CLAIMED/RUNNING deployment runs triggered from a parent flow."""
        rust = self._rust_deployment_dispatch(
            "deployment_cancel_by_parent_flow",
            {"parent_flow_run_id": str(parent_flow_run_id)},
        )
        if rust is not None:
            if rust.get("ok"):
                cancelled = rust.get("cancelled") or []
                for row in cancelled:
                    ptid = row.get("parent_task_run_id")
                    if ptid:
                        self.mirror_subflow_task_from_deployment(
                            UUID(str(ptid)),
                            {
                                **row,
                                "status": "CANCELLED",
                                "error": "parent flow cancelled",
                            },
                        )
                return list(cancelled)
            err = rust.get("error") or {}
            raise RuntimeError(str(err.get("message", "deployment cancel failed")))

        now = self._now()
        rows = self._query_rows(
            """
            SELECT id, flow_run_id, parent_task_run_id
            FROM deployment_runs
            WHERE parent_flow_run_id = ? AND status IN ('SCHEDULED','CLAIMED','RUNNING')
            """,
            [str(parent_flow_run_id)],
        )
        if not rows:
            return []
        self._sqlite_conn.execute(
            """
            UPDATE deployment_runs
            SET status = 'CANCELLED', error = 'parent flow cancelled', finished_at = ?, updated_at = ?, lease_until = NULL
            WHERE parent_flow_run_id = ? AND status IN ('SCHEDULED','CLAIMED','RUNNING')
            """,
            [now, now, str(parent_flow_run_id)],
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            item = {
                "id": row["id"],
                "flow_run_id": row["flow_run_id"],
                "parent_task_run_id": row["parent_task_run_id"],
                "status": "CANCELLED",
            }
            out.append(item)
            if row["parent_task_run_id"]:
                self.mirror_subflow_task_from_deployment(
                    UUID(str(row["parent_task_run_id"])),
                    {**item, "error": "parent flow cancelled"},
                )
        return out

    def _set_flow_cancelled_internal(self, flow_run_id: UUID) -> None:
        flow = self._flows.get(flow_run_id)
        if flow is None:
            return
        if flow.state not in {
            RunState.SCHEDULED,
            RunState.PENDING,
            RunState.RUNNING,
            RunState.PAUSED,
        }:
            return
        try:
            self.set_flow_state(
                flow_run_id, RunState.CANCELLED, uuid4(), "parent_cancel"
            )
        except ValueError:
            return
        now = self._now()
        self._sqlite_conn.execute(
            """
            UPDATE task_runs
            SET state = 'CANCELLED', updated_at = ?
            WHERE flow_run_id = ? AND state IN ('SCHEDULED','PENDING','RUNNING')
            """,
            [now, str(flow_run_id)],
        )
        for task in self._tasks.values():
            if task.flow_run_id == flow_run_id and task.state in {
                RunState.SCHEDULED,
                RunState.PENDING,
                RunState.RUNNING,
            }:
                task.state = RunState.CANCELLED

    def _propagate_cancel_to_subflows(self, root_flow_run_id: UUID) -> None:
        """BFS cancel of inline/deployment child flow runs and linked deployment runs."""
        frontier: list[UUID] = [root_flow_run_id]
        visited: set[UUID] = {root_flow_run_id}
        for _ in range(SUBFLOW_MAX_DEPTH):
            if not frontier:
                break
            next_frontier: list[UUID] = []
            for parent_id in frontier:
                for row in self._cancel_deployment_runs_for_parent_flow(parent_id):
                    fid = row.get("flow_run_id")
                    if fid:
                        child_flow_id = UUID(str(fid))
                        if child_flow_id not in visited:
                            self._set_flow_cancelled_internal(child_flow_id)
                            visited.add(child_flow_id)
                            next_frontier.append(child_flow_id)
                for flow in list(self._flows.values()):
                    if flow.parent_flow_run_id != parent_id:
                        continue
                    if flow.run_id in visited:
                        continue
                    if flow.state not in {
                        RunState.SCHEDULED,
                        RunState.PENDING,
                        RunState.RUNNING,
                    }:
                        continue
                    self._set_flow_cancelled_internal(flow.run_id)
                    visited.add(flow.run_id)
                    next_frontier.append(flow.run_id)
            frontier = next_frontier

    def _cancel_inline_child_flow_runs(self, parent_flow_run_id: UUID) -> None:
        """Deprecated: use _propagate_cancel_to_subflows from cancel_flow_run."""
        self._propagate_cancel_to_subflows(parent_flow_run_id)

    def is_scheduling_held(self, flow_run_id: UUID) -> bool:
        """True when operator pause blocks starting new task runs."""
        with self._lock:
            return self._is_scheduling_held_unlocked(flow_run_id)

    def _is_scheduling_held_unlocked(self, flow_run_id: UUID) -> bool:
        life = self._lifecycle_by_flow.get(str(flow_run_id))
        if not life:
            return False
        if life.get("lifecycle_action") != "pause":
            return False
        if life.get("pause_drain_pending"):
            return True
        flow = self._flows.get(flow_run_id)
        return bool(flow and flow.state == RunState.PAUSED)

    def has_operator_pause(self, flow_run_id: UUID) -> bool:
        life = self._lifecycle_by_flow.get(str(flow_run_id))
        return bool(life and life.get("lifecycle_action") == "pause")

    def pause_flow_run(
        self, flow_run_id: UUID, mode: str | Any
    ) -> dict[str, Any]:
        """Operator pause. ``mode`` must be ``drain`` or ``terminate`` (required)."""
        from .lifecycle import InterruptMode, parse_interrupt_mode

        interrupt = parse_interrupt_mode(mode)
        detail = self.get_flow_run_detail(flow_run_id)
        if detail is None:
            raise ValueError("flow run not found")
        state = str(detail["state"])
        if state in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise ValueError(f"cannot pause from state {state}")
        # Operator pause only from active scheduling states — not gate-only PAUSED.
        if state not in {"SCHEDULED", "PENDING", "RUNNING"}:
            raise ValueError(
                f"cannot pause from state {state} "
                "(operator pause requires SCHEDULED/PENDING/RUNNING; "
                "gate waits are not operator pauses)"
            )

        if interrupt is InterruptMode.DRAIN:
            running = self._count_running_tasks(flow_run_id)
            pending_drain = running > 0 and state == "RUNNING"
            self._set_lifecycle(
                flow_run_id,
                lifecycle_action="pause",
                interrupt_mode=interrupt.value,
                pause_drain_pending=pending_drain,
                lifecycle_summary=(
                    f"Paused (drain) — waiting for {running} task(s)"
                    if pending_drain
                    else "Paused (drain)"
                ),
            )
            if not pending_drain:
                if state != "PAUSED":
                    try:
                        self.set_flow_state(
                            flow_run_id, RunState.PAUSED, uuid4(), "operator_pause_drain"
                        )
                    except ValueError:
                        pass
            return self.get_flow_run_detail(flow_run_id) or detail

        # terminate: control-plane interrupt of RUNNING tasks; PENDING held.
        # Process kill lands in P3.2c — bodies on threads may continue until they exit,
        # but late COMPLETED is fenced in task finalize (task stays CANCELLED).
        self._set_lifecycle(
            flow_run_id,
            lifecycle_action="pause",
            interrupt_mode=interrupt.value,
            pause_drain_pending=False,
            lifecycle_summary="Paused (terminate) — in-flight tasks interrupted",
        )
        running_ids = [
            tid
            for tid, task in list(self._tasks.items())
            if str(task.flow_run_id) == str(flow_run_id)
            and task.state == RunState.RUNNING
        ]
        for tid in running_ids:
            try:
                self.record_task_event(
                    tid,
                    "task_cancelled",
                    {"interrupt_reason": "terminated_by_pause"},
                )
            except Exception:
                # Fallback: force CANCELLED if FSM rejects mid-race.
                with self._lock:
                    task = self._tasks.get(tid)
                    if task and task.state == RunState.RUNNING:
                        task.state = RunState.CANCELLED
                        self._update_task_row(task)
        try:
            self.set_flow_state(
                flow_run_id, RunState.PAUSED, uuid4(), "operator_pause_terminate"
            )
        except ValueError:
            pass
        refreshed = self.get_flow_run_detail(flow_run_id)
        if refreshed is None:
            raise ValueError("flow run not found")
        return refreshed

    def resume_flow_run(self, flow_run_id: UUID) -> dict[str, Any]:
        """Resume an operator-paused flow run (not gate-only PAUSED).

        For in-process ``@flow()`` calls that already exited while paused, if a
        result was stored and no non-terminal tasks remain, completes the run
        instead of leaving a zombie ``RUNNING`` state. Worker/deployment-driven
        continuation of pending work is the primary resume path.
        """
        detail = self.get_flow_run_detail(flow_run_id)
        if detail is None:
            raise ValueError("flow run not found")
        if not self.has_operator_pause(flow_run_id):
            raise ValueError(
                "resume requires an operator pause (lifecycle_action=pause); "
                "gate waits use gate open, not resume"
            )
        state = str(detail["state"])
        if state not in {"PAUSED", "RUNNING"}:
            raise ValueError(f"cannot resume from state {state}")
        life = self._lifecycle_by_flow.get(str(flow_run_id), {})
        if life.get("pause_drain_pending"):
            raise ValueError("cannot resume while drain pause is still pending")

        has_result = flow_run_id in self._flow_results
        pending_left = self._count_nonterminal_tasks(flow_run_id) > 0

        if state == "PAUSED":
            try:
                self.set_flow_state(
                    flow_run_id, RunState.RUNNING, uuid4(), "operator_resume"
                )
            except ValueError as exc:
                refreshed = self.get_flow_run_detail(flow_run_id)
                if not (refreshed and refreshed["state"] == "RUNNING"):
                    raise ValueError(str(exc)) from exc

        if has_result and not pending_left:
            current = self.get_flow(flow_run_id)
            if current.state == RunState.RUNNING:
                try:
                    self.set_flow_state(
                        flow_run_id,
                        RunState.COMPLETED,
                        uuid4(),
                        "complete_after_pause",
                        expected_version=current.version,
                    )
                except ValueError:
                    pass

        self._set_lifecycle(
            flow_run_id,
            lifecycle_action="resume",
            interrupt_mode=None,
            pause_drain_pending=False,
            lifecycle_summary=None,
        )
        refreshed = self.get_flow_run_detail(flow_run_id)
        if refreshed is None:
            raise ValueError("flow run not found")
        return refreshed

    def _count_running_tasks(self, flow_run_id: UUID) -> int:
        with self._lock:
            n = 0
            for task in self._tasks.values():
                if (
                    str(task.flow_run_id) == str(flow_run_id)
                    and task.state == RunState.RUNNING
                ):
                    n += 1
            return n

    def _count_nonterminal_tasks(self, flow_run_id: UUID) -> int:
        terminal = {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
        with self._lock:
            n = 0
            for task in self._tasks.values():
                if str(task.flow_run_id) == str(flow_run_id) and task.state not in terminal:
                    n += 1
            return n

    def _set_lifecycle(
        self,
        flow_run_id: UUID,
        *,
        lifecycle_action: str | None,
        interrupt_mode: str | None,
        pause_drain_pending: bool = False,
        lifecycle_summary: str | None = None,
    ) -> None:
        key = str(flow_run_id)
        if lifecycle_action is None and interrupt_mode is None:
            self._lifecycle_by_flow.pop(key, None)
            return
        entry = {
            "lifecycle_action": lifecycle_action,
            "interrupt_mode": interrupt_mode,
            "pause_drain_pending": bool(pause_drain_pending),
            "lifecycle_summary": lifecycle_summary,
        }
        self._lifecycle_by_flow[key] = entry
        self._persist_record(
            {
                "record_type": "flow_lifecycle",
                "flow_run_id": key,
                **entry,
            }
        )

    def _maybe_settle_drain_pause(self, flow_run_id: UUID) -> None:
        life = self._lifecycle_by_flow.get(str(flow_run_id))
        if not life or not life.get("pause_drain_pending"):
            return
        if life.get("interrupt_mode") != "drain":
            return
        if self._count_running_tasks(flow_run_id) > 0:
            return
        self._set_lifecycle(
            flow_run_id,
            lifecycle_action="pause",
            interrupt_mode="drain",
            pause_drain_pending=False,
            lifecycle_summary="Paused (drain)",
        )
        try:
            flow = self.get_flow(flow_run_id)
        except Exception:
            return
        if flow.state == RunState.RUNNING:
            try:
                self.set_flow_state(
                    flow_run_id, RunState.PAUSED, uuid4(), "operator_pause_drain_settled"
                )
            except ValueError:
                pass

    def cancel_flow_run(self, flow_run_id: UUID) -> dict[str, Any]:
        detail = self.get_flow_run_detail(flow_run_id)
        if detail is None:
            raise ValueError("flow run not found")
        state = str(detail["state"])
        if state == "CANCELLED":
            return detail
        if state in {"COMPLETED", "FAILED"}:
            with self._lock:
                self._propagate_cancel_to_subflows(flow_run_id)
            return detail
        if state not in {"SCHEDULED", "PENDING", "RUNNING", "PAUSED"}:
            raise ValueError(f"cannot cancel from state {state}")

        self._set_lifecycle(
            flow_run_id,
            lifecycle_action="cancel",
            interrupt_mode="terminate",
            pause_drain_pending=False,
            lifecycle_summary="Cancelled (terminate)",
        )

        token = uuid4()
        try:
            self.set_flow_state(flow_run_id, RunState.CANCELLED, token, "user_cancel")
        except ValueError:
            refreshed = self.get_flow_run_detail(flow_run_id)
            if refreshed and refreshed["state"] == "CANCELLED":
                return refreshed
            raise

        now = self._now()
        with self._lock:
            self._propagate_cancel_to_subflows(flow_run_id)
            self._sqlite_conn.execute(
                """
                UPDATE task_runs
                SET state = 'CANCELLED', updated_at = ?
                WHERE flow_run_id = ? AND state IN ('SCHEDULED','PENDING','RUNNING')
                """,
                [now, str(flow_run_id)],
            )
            for task in self._tasks.values():
                if str(task.flow_run_id) == str(flow_run_id) and task.state.value in {
                    "SCHEDULED",
                    "PENDING",
                    "RUNNING",
                }:
                    task.state = RunState.CANCELLED
        refreshed = self.get_flow_run_detail(flow_run_id)
        if refreshed is None:
            raise ValueError("flow run not found")
        return refreshed

    def retry_flow_run(self, flow_run_id: UUID) -> dict[str, Any]:
        rows = self._query_rows(
            """
            SELECT deployment_id, requested_parameters
            FROM deployment_runs
            WHERE flow_run_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [str(flow_run_id)],
        )
        if not rows:
            raise ValueError("flow run is not deployment-backed")
        deployment_id = UUID(str(rows[0]["deployment_id"]))
        requested = json.loads(rows[0]["requested_parameters"] or "{}")
        return self.trigger_deployment_run(
            deployment_id,
            parameters=requested,
            resume_from_flow_run_id=flow_run_id,
        )

    def list_work_pools(self, limit: int = 50, cursor: str | None = None) -> PageResult:
        query = "SELECT rowid AS seq, id, name, type, paused, created_at, updated_at FROM work_pools"
        params: list[Any] = []
        if cursor:
            query += " WHERE rowid < ?"
            params.append(int(cursor))
        query += " ORDER BY rowid DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._work_pool_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def get_work_pool(self, work_pool_id: str) -> dict[str, Any] | None:
        rows = self._query_rows(
            "SELECT rowid AS seq, id, name, type, paused, created_at, updated_at FROM work_pools WHERE id = ? LIMIT 1",
            [work_pool_id],
        )
        if not rows:
            return None
        return self._work_pool_row_to_dict(rows[0])

    def create_work_pool(self, name: str, pool_type: str = "process") -> dict[str, Any]:
        if pool_type != "process":
            raise ValueError("only process work pools are supported in MVP")
        existing = self._query_rows(
            "SELECT rowid AS seq, id, name, type, paused, created_at, updated_at FROM work_pools WHERE name = ? LIMIT 1",
            [name],
        )
        if existing:
            return self._work_pool_row_to_dict(existing[0])
        now = self._now()
        pool_id = str(uuid4())
        with self._lock:
            self._sqlite_conn.execute(
                "INSERT INTO work_pools(id,name,type,paused,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                [pool_id, name, pool_type, 0, now, now],
            )
        created = self.get_work_pool(pool_id)
        if created is None:
            raise RuntimeError("failed to create work pool")
        return created

    def patch_work_pool(
        self, work_pool_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        current = self.get_work_pool(work_pool_id)
        if current is None:
            raise ValueError("work pool not found")
        paused = current["paused"]
        if "paused" in patch:
            paused = bool(patch["paused"])
        now = self._now()
        with self._lock:
            self._sqlite_conn.execute(
                "UPDATE work_pools SET paused = ?, updated_at = ? WHERE id = ?",
                [1 if paused else 0, now, work_pool_id],
            )
        updated = self.get_work_pool(work_pool_id)
        if updated is None:
            raise ValueError("work pool not found")
        return updated

    def list_workers(
        self,
        work_pool_id: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> PageResult:
        query = "SELECT rowid AS seq, name, last_heartbeat, status, updated_at, work_pool_id FROM workers"
        params: list[Any] = []
        conditions: list[str] = []
        if work_pool_id:
            conditions.append("work_pool_id = ?")
            params.append(work_pool_id)
        if cursor:
            conditions.append("rowid < ?")
            params.append(int(cursor))
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY rowid DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._worker_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def _ensure_default_work_pool(self) -> None:
        now = self._now()
        with self._lock:
            self._sqlite_conn.execute(
                """
                INSERT OR IGNORE INTO work_pools(id,name,type,paused,created_at,updated_at)
                VALUES(?,?,?,?,?,?)
                """,
                [DEFAULT_WORK_POOL_ID, "default-process-pool", "process", 0, now, now],
            )
            self._sqlite_conn.execute(
                "UPDATE deployments SET work_pool_id = ? WHERE work_pool_id IS NULL",
                [DEFAULT_WORK_POOL_ID],
            )

    def claim_next_deployment_run(
        self, worker_name: str, lease_seconds: int = 30, work_pool_id: str | None = None
    ) -> dict[str, Any] | None:
        pool_id = work_pool_id or os.getenv("IRONFLOW_WORK_POOL", DEFAULT_WORK_POOL_ID)
        rust = self._rust_deployment_dispatch(
            "deployment_claim_next",
            {
                "worker_name": worker_name,
                "lease_seconds": lease_seconds,
                "work_pool_id": pool_id,
            },
        )
        if rust is not None:
            if rust.get("ok"):
                run = rust.get("run")
                return None if run is None else self._merge_resume_from_flow_run_id(run)
            err = rust.get("error") or {}
            raise RuntimeError(str(err.get("message", "deployment claim failed")))

        with self._lock:
            self._reclaim_expired_claims_python()
            now_dt = datetime.now(UTC)
            now = now_dt.isoformat()
            lease_until = (
                now_dt + timedelta(seconds=max(1, lease_seconds))
            ).isoformat()
            self._sqlite_conn.execute(
                """
                INSERT INTO workers(name,last_heartbeat,status,updated_at,work_pool_id)
                VALUES(?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    last_heartbeat=excluded.last_heartbeat,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    work_pool_id=excluded.work_pool_id
                """,
                [worker_name, now, "ONLINE", now, pool_id],
            )
            candidates = self._query_rows(
                """
                SELECT dr.id FROM deployment_runs dr
                INNER JOIN deployments d ON d.id = dr.deployment_id
                INNER JOIN work_pools wp ON wp.id = COALESCE(d.work_pool_id, 'default-process-pool') AND wp.paused = 0
                WHERE dr.status = 'SCHEDULED'
                AND COALESCE(d.work_pool_id, ?) = ?
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
                [DEFAULT_WORK_POOL_ID, pool_id],
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
                       worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,
                       resume_from_flow_run_id,
                       created_at,updated_at,started_at,finished_at
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
        self,
        worker_name: str,
        lease_seconds: int = 30,
        wait_ms: int = 500,
        work_pool_id: str | None = None,
    ) -> dict[str, Any] | None:
        pool_id = work_pool_id or os.getenv("IRONFLOW_WORK_POOL", DEFAULT_WORK_POOL_ID)
        rust = self._rust_deployment_dispatch(
            "deployment_claim_next_wait",
            {
                "worker_name": worker_name,
                "lease_seconds": lease_seconds,
                "wait_ms": wait_ms,
                "work_pool_id": pool_id,
            },
        )
        if rust is not None:
            if rust.get("ok"):
                run = rust.get("run")
                return None if run is None else run
            err = rust.get("error") or {}
            raise RuntimeError(str(err.get("message", "deployment claim wait failed")))

        deadline = time.monotonic() + max(wait_ms, 1) / 1000.0
        while time.monotonic() < deadline:
            c = self.claim_next_deployment_run(
                worker_name, lease_seconds, work_pool_id=pool_id
            )
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

    def attach_flow_run_to_deployment_run(
        self, deployment_run_id: UUID, flow_run_id: UUID
    ) -> None:
        rust = self._rust_deployment_dispatch(
            "deployment_attach_flow_run",
            {
                "deployment_run_id": str(deployment_run_id),
                "flow_run_id": str(flow_run_id),
            },
        )
        if rust is not None and rust.get("ok"):
            return
        now = self._now()
        with self._lock:
            self._sqlite_conn.execute(
                """
                UPDATE deployment_runs
                SET flow_run_id = ?, updated_at = ?
                WHERE id = ? AND (flow_run_id IS NULL OR flow_run_id = ?)
                """,
                [str(flow_run_id), now, str(deployment_run_id), str(flow_run_id)],
            )

    def mark_deployment_run_finished(
        self,
        deployment_run_id: UUID,
        status: str,
        flow_run_id: UUID | None = None,
        error: str | None = None,
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
                [
                    status,
                    str(flow_run_id) if flow_run_id else None,
                    error,
                    now,
                    now,
                    str(deployment_run_id),
                ],
            )

    def get_flow_run_dag(
        self, flow_run_id: UUID, mode: str = "logical"
    ) -> dict[str, Any]:
        manifest_rows = self._query_rows(
            "SELECT manifest_json, forecast_json, warnings_json, fallback_required, source FROM dag_manifests WHERE flow_run_id = ? LIMIT 1",
            [str(flow_run_id)],
        )
        task_rows = self._task_rows_for_dag(flow_run_id)

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
        nodes, edges = self._enrich_dag_subflow_nodes(
            flow_run_id, nodes, edges, task_rows
        )
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
            "kind,child_flow_run_id,child_deployment_run_id,gate_open_at,tags,contribute_to_flow_state) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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

    def _infer_runtime_manifest(
        self, task_rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []
        instance_counts: dict[str, int] = {}
        node_by_planned: dict[str, str] = {}
        previous: str | None = None
        counter = 0

        for row in task_rows:
            task_name = row["task_name"]
            planned = row["planned_node_id"]
            node_id = str(planned) if planned else None

            if node_id is None or node_id not in node_by_planned:
                counter += 1
                node_id = node_id or f"rt_{counter}"
                instance = instance_counts.get(task_name, 0)
                instance_counts[task_name] = instance + 1
                node_by_planned[node_id] = node_id
                nodes.append(
                    {
                        "node_id": node_id,
                        "task_name": task_name,
                        "label": f"{task_name}-{instance}",
                        "op_type": "runtime",
                        "deps": [],
                    }
                )
                if previous is not None:
                    edges.append({"from": previous, "to": node_id})
                previous = node_id

        return {"nodes": nodes, "edges": edges}

    def _logical_dag(
        self, manifest: dict[str, Any], task_rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        nodes = manifest.get("nodes", [])
        edges = manifest.get("edges", [])
        by_planned: dict[str, list[str]] = {}
        meta_by_planned: dict[str, dict[str, Any]] = {}
        for row in task_rows:
            state = str(row["state"])
            planned = row.get("planned_node_id")
            if planned:
                key = str(planned)
                by_planned.setdefault(key, []).append(state)
                meta_by_planned[key] = row
            task_name = str(row["task_name"])
            if task_name.startswith("subflow:"):
                key = str(planned or row["id"])
                by_planned.setdefault(key, []).append(state)
                meta_by_planned[key] = row

        out_nodes: list[dict[str, Any]] = []
        node_state: dict[str, str] = {}
        for node in nodes:
            node_id = str(node["node_id"])
            states = by_planned.get(node_id, [])
            state = self._aggregate_state(states)
            node_state[node_id] = state
            label = node.get("label") or node.get("task_name", node_id)
            out_node: dict[str, Any] = {
                "id": node_id,
                "label": label,
                "task_name": node.get("task_name"),
                "op_type": node.get("op_type"),
                "planned_node_id": node_id,
                "state": state,
                "kind": "gate_task" if node.get("op_type") == "gate" else "task",
            }
            meta = meta_by_planned.get(node_id)
            task_name_meta = str(meta.get("task_name", "")) if meta is not None else ""
            is_subflow_meta = meta is not None and (
                str(meta.get("kind", "task")) == "subflow"
                or task_name_meta.startswith("subflow:")
            )
            if is_subflow_meta:
                out_node["kind"] = "subflow_task"
                if meta.get("child_flow_run_id"):
                    out_node["child_flow_run_id"] = str(meta["child_flow_run_id"])
                if meta.get("child_deployment_run_id"):
                    out_node["child_deployment_run_id"] = str(
                        meta["child_deployment_run_id"]
                    )
                if task_name_meta.startswith("subflow:"):
                    out_node["label"] = task_name_meta.removeprefix("subflow:")
            elif meta is not None and (
                str(meta.get("kind", "task")) == "gate"
                or task_name_meta.startswith("gate:")
            ):
                out_node["kind"] = "gate_task"
                if meta.get("gate_open_at"):
                    out_node["gate_open_at"] = str(meta["gate_open_at"])
                if task_name_meta.startswith("gate:"):
                    out_node["label"] = task_name_meta.removeprefix("gate:")
            out_nodes.append(out_node)

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
        out_nodes, edges = self._prune_orphan_forecast_nodes(
            out_nodes, edges, meta_by_planned
        )
        return out_nodes, edges

    def _prune_orphan_forecast_nodes(
        self,
        out_nodes: list[dict[str, Any]],
        edges: list[dict[str, str]],
        meta_by_planned: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        drop_ids: set[str] = set()
        for node in out_nodes:
            if str(node.get("task_name", "")) != "unknown_task":
                continue
            node_id = str(node["id"])
            if node_id not in meta_by_planned and node.get("state") == "PENDING":
                drop_ids.add(node_id)
        if not drop_ids:
            return out_nodes, edges
        pruned_nodes = [node for node in out_nodes if str(node["id"]) not in drop_ids]
        pruned_edges = [
            edge
            for edge in edges
            if edge["from"] not in drop_ids and edge["to"] not in drop_ids
        ]
        return pruned_nodes, pruned_edges

    def _expanded_dag(
        self, manifest: dict[str, Any], task_rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        max_nodes = 600
        limited_rows = task_rows[:max_nodes]
        nodes: list[dict[str, Any]] = []
        for row in limited_rows:
            kind = str(row.get("kind", "task"))
            task_name = str(row["task_name"])
            label = f"{task_name}:{str(row['id'])[:8]}"
            node: dict[str, Any] = {
                "id": row["id"],
                "label": label,
                "task_name": task_name,
                "planned_node_id": row.get("planned_node_id"),
                "state": row["state"],
                "kind": "subflow_task"
                if kind == "subflow"
                else "gate_task"
                if kind == "gate"
                else "task",
            }
            if kind == "subflow":
                if row.get("child_flow_run_id"):
                    node["child_flow_run_id"] = row["child_flow_run_id"]
                if row.get("child_deployment_run_id"):
                    node["child_deployment_run_id"] = row["child_deployment_run_id"]
                if task_name.startswith("subflow:"):
                    node["label"] = (
                        f"{task_name.removeprefix('subflow:')}:{str(row['id'])[:8]}"
                    )
            if kind == "gate":
                if row.get("gate_open_at"):
                    node["gate_open_at"] = str(row["gate_open_at"])
                if task_name.startswith("gate:"):
                    node["label"] = (
                        f"{task_name.removeprefix('gate:')}:{str(row['id'])[:8]}"
                    )
            nodes.append(node)
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

    def _task_rows_for_dag(self, flow_run_id: UUID) -> list[dict[str, Any]]:
        rows = self._query_rows(
            """
            SELECT id, task_name, planned_node_id, state, created_at, updated_at,
                   kind, child_flow_run_id, child_deployment_run_id, gate_open_at
            FROM task_runs
            WHERE flow_run_id = ?
            ORDER BY created_at ASC
            """,
            [str(flow_run_id)],
        )
        by_id = {str(row["id"]): self._dag_task_row_dict(row) for row in rows}
        with self._lock:
            memory_tasks = [
                task for task in self._tasks.values() if task.flow_run_id == flow_run_id
            ]
        for task in memory_tasks:
            tid = str(task.task_run_id)
            existing = by_id.get(tid)
            if existing is None:
                by_id[tid] = {
                    "id": tid,
                    "flow_run_id": str(task.flow_run_id),
                    "task_name": task.task_name,
                    "planned_node_id": task.planned_node_id,
                    "state": task.state.value,
                    "version": task.version,
                    "created_at": self._now(),
                    "updated_at": self._now(),
                    "kind": task.kind,
                    "child_flow_run_id": str(task.child_flow_run_id)
                    if task.child_flow_run_id
                    else None,
                    "child_deployment_run_id": str(task.child_deployment_run_id)
                    if task.child_deployment_run_id
                    else None,
                    "gate_open_at": task.gate_open_at,
                }
                continue
            if task.kind != "task":
                existing["kind"] = task.kind
            if task.gate_open_at and not existing.get("gate_open_at"):
                existing["gate_open_at"] = task.gate_open_at
            if task.child_flow_run_id and not existing.get("child_flow_run_id"):
                existing["child_flow_run_id"] = str(task.child_flow_run_id)
            if task.child_deployment_run_id and not existing.get(
                "child_deployment_run_id"
            ):
                existing["child_deployment_run_id"] = str(task.child_deployment_run_id)
        return sorted(by_id.values(), key=lambda item: item["created_at"])

    def _dag_task_row_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        return {
            "id": row["id"],
            "task_name": row["task_name"],
            "planned_node_id": row["planned_node_id"]
            if "planned_node_id" in keys
            else None,
            "state": row["state"],
            "created_at": row["created_at"] if "created_at" in keys else self._now(),
            "updated_at": row["updated_at"] if "updated_at" in keys else self._now(),
            "kind": row["kind"] if "kind" in keys else "task",
            "child_flow_run_id": row["child_flow_run_id"]
            if "child_flow_run_id" in keys
            else None,
            "child_deployment_run_id": row["child_deployment_run_id"]
            if "child_deployment_run_id" in keys
            else None,
            "gate_open_at": row["gate_open_at"] if "gate_open_at" in keys else None,
        }

    def _resolve_subflow_child_flow_run_id(
        self, task_row: dict[str, Any]
    ) -> str | None:
        child_flow = task_row.get("child_flow_run_id")
        if child_flow:
            return str(child_flow)
        task_id = task_row.get("id")
        if task_id:
            with self._lock:
                task = self._tasks.get(UUID(str(task_id)))
                if task and task.child_flow_run_id:
                    return str(task.child_flow_run_id)
        dep_run_id = task_row.get("child_deployment_run_id")
        if not dep_run_id and task_id:
            with self._lock:
                task = self._tasks.get(UUID(str(task_id)))
                if task and task.child_deployment_run_id:
                    dep_run_id = str(task.child_deployment_run_id)
        if dep_run_id:
            dep = self.get_deployment_run(UUID(str(dep_run_id)))
            if dep and dep.get("flow_run_id"):
                return str(dep["flow_run_id"])
        if task_id:
            dep_rows = self._query_rows(
                """
                SELECT flow_run_id
                FROM deployment_runs
                WHERE parent_task_run_id = ? AND flow_run_id IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                [str(task_id)],
            )
            if dep_rows and dep_rows[0]["flow_run_id"]:
                return str(dep_rows[0]["flow_run_id"])
            child_rows = self._query_rows(
                """
                SELECT id
                FROM flow_runs
                WHERE parent_task_run_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                [str(task_id)],
            )
            if child_rows:
                return str(child_rows[0]["id"])
        return None

    def _resolve_subflow_child_deployment_run_id(
        self, task_row: dict[str, Any]
    ) -> str | None:
        dep_run_id = task_row.get("child_deployment_run_id")
        if dep_run_id:
            return str(dep_run_id)
        task_id = task_row.get("id")
        if task_id:
            with self._lock:
                task = self._tasks.get(UUID(str(task_id)))
                if task and task.child_deployment_run_id:
                    return str(task.child_deployment_run_id)
            dep_rows = self._query_rows(
                """
                SELECT id
                FROM deployment_runs
                WHERE parent_task_run_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                [str(task_id)],
            )
            if dep_rows:
                return str(dep_rows[0]["id"])
        return None

    def _enrich_dag_subflow_nodes(
        self,
        flow_run_id: UUID,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, str]],
        task_rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        node_by_id = {str(node["id"]): node for node in nodes}
        for row in task_rows:
            kind = str(row.get("kind", "task"))
            task_name = str(row["task_name"])
            is_subflow = kind == "subflow" or task_name.startswith("subflow:")
            if not is_subflow:
                continue
            planned = (
                str(row["planned_node_id"]) if row.get("planned_node_id") else None
            )
            node_id = planned or str(row["id"])
            node = node_by_id.get(node_id)
            if node is None and planned:
                node = next(
                    (n for n in nodes if n.get("planned_node_id") == planned), None
                )
            if node is None:
                label = (
                    task_name.removeprefix("subflow:")
                    if task_name.startswith("subflow:")
                    else task_name
                )
                node = {
                    "id": node_id,
                    "label": label,
                    "task_name": task_name,
                    "planned_node_id": row.get("planned_node_id"),
                    "state": row["state"],
                    "kind": "subflow_task",
                }
                nodes.append(node)
                node_by_id[node_id] = node
                if nodes and len(nodes) > 1:
                    prev_id = str(nodes[-2]["id"])
                    if prev_id != node_id:
                        edges.append({"from": prev_id, "to": node_id})
            node["kind"] = "subflow_task"
            child_flow_run_id = self._resolve_subflow_child_flow_run_id(row)
            if child_flow_run_id:
                node["child_flow_run_id"] = child_flow_run_id
            dep_run_id = self._resolve_subflow_child_deployment_run_id(row)
            if dep_run_id:
                node["child_deployment_run_id"] = dep_run_id

        inline_rows = self._query_rows(
            """
            SELECT id, name, state
            FROM flow_runs
            WHERE parent_flow_run_id = ? AND execution_mode = 'inline'
            ORDER BY created_at ASC
            """,
            [str(flow_run_id)],
        )
        previous_id: str | None = str(nodes[-1]["id"]) if nodes else None
        for row in inline_rows:
            child_id = str(row["id"])
            node_id = f"inline:{child_id}"
            if node_id in node_by_id:
                continue
            inline_node = {
                "id": node_id,
                "label": row["name"],
                "task_name": row["name"],
                "kind": "inline_subflow",
                "child_flow_run_id": child_id,
                "execution_mode": "inline",
                "state": row["state"],
            }
            nodes.append(inline_node)
            node_by_id[node_id] = inline_node
            if previous_id is not None:
                edges.append({"from": previous_id, "to": node_id})
            previous_id = node_id
        return nodes, edges

    def _flow_run_breadcrumb(self, flow_run_id: UUID) -> list[dict[str, Any]]:
        chain: list[dict[str, Any]] = []
        current: UUID | None = flow_run_id
        visited: set[UUID] = set()
        for _ in range(SUBFLOW_MAX_DEPTH + 1):
            if current is None or current in visited:
                break
            visited.add(current)
            rows = self._query_rows(
                "SELECT id, name, parent_flow_run_id, execution_mode FROM flow_runs WHERE id = ? LIMIT 1",
                [str(current)],
            )
            if not rows:
                break
            row = rows[0]
            chain.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "execution_mode": row["execution_mode"]
                    if "execution_mode" in row.keys()
                    else None,
                }
            )
            parent = (
                row["parent_flow_run_id"]
                if "parent_flow_run_id" in row.keys()
                else None
            )
            current = UUID(str(parent)) if parent else None
        chain.reverse()
        return chain

    def _flow_run_children_summary(self, flow_run_id: UUID) -> dict[str, int]:
        inline_rows = self._query_rows(
            "SELECT COUNT(*) AS c FROM flow_runs WHERE parent_flow_run_id = ? AND execution_mode = 'inline'",
            [str(flow_run_id)],
        )
        subflow_rows = self._query_rows(
            "SELECT COUNT(*) AS c FROM task_runs WHERE flow_run_id = ? AND kind = 'subflow'",
            [str(flow_run_id)],
        )
        deployment_rows = self._query_rows(
            "SELECT COUNT(*) AS c FROM flow_runs WHERE parent_flow_run_id = ? AND execution_mode = 'deployment'",
            [str(flow_run_id)],
        )
        return {
            "inline_subflows": int(inline_rows[0]["c"]) if inline_rows else 0,
            "subflow_tasks": int(subflow_rows[0]["c"]) if subflow_rows else 0,
            "deployment_subflows": int(deployment_rows[0]["c"])
            if deployment_rows
            else 0,
        }

    def _flow_run_children(self, flow_run_id: UUID) -> list[dict[str, Any]]:
        rows = self._query_rows(
            """
            SELECT id, name, state, execution_mode, depth, created_at, updated_at
            FROM flow_runs
            WHERE parent_flow_run_id = ?
            ORDER BY created_at ASC
            """,
            [str(flow_run_id)],
        )
        children: list[dict[str, Any]] = []
        for row in rows:
            keys = row.keys()
            children.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "state": row["state"],
                    "execution_mode": row["execution_mode"]
                    if "execution_mode" in keys
                    else None,
                    "depth": int(row["depth"])
                    if "depth" in keys and row["depth"] is not None
                    else 0,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return children

    def _aggregate_state(self, states: list[str]) -> str:
        if not states:
            return "PENDING"
        priority = [
            "FAILED",
            "CANCELLED",
            "RUNNING",
            "PENDING",
            "SCHEDULED",
            "COMPLETED",
        ]
        for state in priority:
            if state in states:
                return state
        return states[-1]

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


def _legacy_is_valid_transition(from_state: RunState, to_state: RunState) -> bool:
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
