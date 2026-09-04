from __future__ import annotations

import logging
from typing import Any

from ..persistence import DEFAULT_WORK_POOL_ID
from .types import (
    FlowRunRecord,
    TaskRunRecord,
)


class RustDispatchMixin:
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
            "flow_id": d.get("flow_id"),
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

    def _query_rust(self, kind: str, params: dict[str, Any]) -> Any | None:
        if self._rust_bridge is None:
            return None
        try:
            return self._rust_bridge.query(str(self._sqlite_path), kind, params)
        except Exception:
            return None
