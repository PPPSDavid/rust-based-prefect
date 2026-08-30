from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID

from .control_plane.dag import DagMixin
from .control_plane.deployment_runs import DeploymentRunsMixin
from .control_plane.deployments import DeploymentsMixin
from .control_plane.gates import GatesMixin
from .control_plane.gcl import GclMixin
from .control_plane.lifecycle import LifecycleMixin
from .control_plane.queries import QueriesMixin
from .control_plane.resume import ResumeMixin
from .control_plane.run_events import RunEventsMixin
from .control_plane.runs import RunsMixin
from .control_plane.rust_dispatch import RustDispatchMixin
from .control_plane.store import StoreMixin
from .control_plane.types import (
    SUBFLOW_MAX_DEPTH,
    DeploymentRecord,
    FlowRunRecord,
    FlowRunSchedulingHeld,
    PageResult,
    RunState,
    SetStateResult,
    TaskRunRecord,
)
from .control_plane.types import (
    legacy_is_valid_transition as _legacy_is_valid_transition,
)
from .persistence import ControlPlaneStore, create_store, resolve_sqlite_path

_RustQueryBridge: Any = None
_RustFsmBridge: Any = None
try:
    from .rust_bridge import (
        RustFsmBridge as _RustFsmBridge_cls,
    )
    from .rust_bridge import (
        RustQueryBridge as _RustQueryBridge_cls,
    )

    _RustQueryBridge = _RustQueryBridge_cls
    _RustFsmBridge = _RustFsmBridge_cls
except Exception:  # pragma: no cover - best-effort optional accelerator
    pass

RustQueryBridge: Any = _RustQueryBridge
RustFsmBridge: Any = _RustFsmBridge

__all__ = [
    "DeploymentRecord",
    "FlowRunRecord",
    "FlowRunSchedulingHeld",
    "InMemoryControlPlane",
    "PageResult",
    "RunState",
    "SetStateResult",
    "TaskRunRecord",
    "SUBFLOW_MAX_DEPTH",
    "_legacy_is_valid_transition",
]


class InMemoryControlPlane(
    RustDispatchMixin,
    GclMixin,
    ResumeMixin,
    RunsMixin,
    RunEventsMixin,
    QueriesMixin,
    DagMixin,
    DeploymentsMixin,
    DeploymentRunsMixin,
    GatesMixin,
    LifecycleMixin,
    StoreMixin,
):
    """Durable control plane (SQLite/Postgres + optional Rust FSM).

    The class name is historical (in-memory maps plus a projected store). Public
    imports stay ``from prefect_compat.runtime import InMemoryControlPlane``.
    """

    _FLOW_BATCH_MIN_SIZE = 2

    _TASK_BATCH_MIN_SIZE = 2

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
