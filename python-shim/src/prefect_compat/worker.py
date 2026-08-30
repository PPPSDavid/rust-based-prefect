from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from importlib import import_module
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from .cancellation import FlowRunCancelled
from .decorators import _ACTIVE_DEPLOYMENT_RUN
from .runtime import RunState


def resolve_worker_mode(explicit: str | None = None) -> str:
    """Return ``http`` or ``file`` (default ``file`` for local shared-DB workers)."""
    raw = (
        explicit if explicit is not None else os.getenv("IRONFLOW_WORKER_MODE", "file")
    )
    mode = str(raw).strip().lower()
    if mode in ("http", "file"):
        return mode
    raise ValueError(f"IRONFLOW_WORKER_MODE must be 'http' or 'file' (got {raw!r})")


def _deployment_run_flow_run_id(
    control_plane: Any, deployment_run_id: UUID
) -> UUID | None:
    """Flow run created for this deployment execution (set at @flow entry)."""
    dep_run = control_plane.get_deployment_run(deployment_run_id)
    if dep_run is None:
        return None
    raw = dep_run.get("flow_run_id")
    if raw is None or str(raw).strip() == "":
        return None
    return UUID(str(raw))


def resolve_flow_callable(
    flow_name: str,
    entrypoint: str | None = None,
    flow_registry: dict[str, Callable[..., Any]] | None = None,
) -> Callable[..., Any]:
    if flow_registry is None:
        from .server import FLOW_REGISTRY

        flow_registry = FLOW_REGISTRY
    if flow_name in flow_registry:
        return flow_registry[flow_name]
    if entrypoint:
        module_name, sep, func_name = entrypoint.partition(":")
        if not sep:
            raise ValueError("entrypoint must look like module.submodule:function_name")
        mod = import_module(module_name)
        fn = getattr(mod, func_name, None)
        if fn is None:
            raise ValueError(f"entrypoint function not found: {entrypoint}")
        return fn
    raise ValueError(f"unknown local flow: {flow_name}")


def execute_claimed_deployment_run(
    control_plane: Any,
    claimed: dict,
    flow_registry: dict[str, Callable[..., Any]] | None = None,
) -> None:
    deployment = claimed.get("deployment")
    if not isinstance(deployment, dict):
        deployment = control_plane.get_deployment(UUID(claimed["deployment_id"]))
    if deployment is None:
        control_plane.mark_deployment_run_finished(
            deployment_run_id=UUID(claimed["id"]),
            status="FAILED",
            error="Deployment not found",
        )
        return

    dep_run_id = UUID(claimed["id"])
    fresh = control_plane.get_deployment_run(dep_run_id)
    if fresh is not None and str(fresh.get("status")) == "CANCELLED":
        return

    control_plane.mark_deployment_run_started(dep_run_id)
    flow_run_id: UUID | None = None
    dep_token = _ACTIVE_DEPLOYMENT_RUN.set(dep_run_id)
    try:
        flow_fn = resolve_flow_callable(
            deployment["flow_name"],
            deployment.get("entrypoint"),
            flow_registry,
        )
        params = claimed.get("resolved_parameters", {}) or {}
        flow_fn(**params)
        flow_run_id = _deployment_run_flow_run_id(control_plane, dep_run_id)
        if flow_run_id is not None:
            if control_plane.get_flow(flow_run_id).state == RunState.CANCELLED:
                control_plane.mark_deployment_run_finished(
                    deployment_run_id=UUID(claimed["id"]),
                    status="CANCELLED",
                    flow_run_id=flow_run_id,
                )
                return
        control_plane.mark_deployment_run_finished(
            deployment_run_id=UUID(claimed["id"]),
            status="COMPLETED",
            flow_run_id=flow_run_id,
        )
    except FlowRunCancelled:
        flow_run_id = _deployment_run_flow_run_id(control_plane, dep_run_id)
        control_plane.mark_deployment_run_finished(
            deployment_run_id=UUID(claimed["id"]),
            status="CANCELLED",
            flow_run_id=flow_run_id,
        )
    except Exception as exc:
        control_plane.mark_deployment_run_finished(
            deployment_run_id=UUID(claimed["id"]),
            status="FAILED",
            flow_run_id=flow_run_id,
            error=str(exc),
        )
    finally:
        _ACTIVE_DEPLOYMENT_RUN.reset(dep_token)


class HttpWorkerBackend:
    """Enough control-plane surface for ``execute_claimed_deployment_run`` over HTTP."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get_deployment(self, deployment_id: UUID) -> dict[str, Any] | None:
        return self._client.get_deployment(deployment_id)

    def get_deployment_run(self, deployment_run_id: UUID) -> dict[str, Any] | None:
        return self._client.get_deployment_run(deployment_run_id)

    def mark_deployment_run_started(self, deployment_run_id: UUID) -> None:
        self._client.mark_started(deployment_run_id)

    def mark_deployment_run_finished(
        self,
        deployment_run_id: UUID,
        status: str,
        flow_run_id: UUID | None = None,
        error: str | None = None,
    ) -> None:
        self._client.mark_finished(
            deployment_run_id,
            status=status,
            flow_run_id=flow_run_id,
            error=error,
        )

    def get_flow(self, flow_run_id: UUID) -> Any:
        data = self._client.get_flow_run(flow_run_id)
        if data is None:
            raise KeyError(f"flow run not found: {flow_run_id}")
        state_raw = data.get("state")
        if isinstance(state_raw, RunState):
            state = state_raw
        else:
            state = RunState(str(state_raw))
        return SimpleNamespace(state=state)


def run_local_deployment_once(
    control_plane: Any,
    worker_name: str,
    work_pool_id: str,
    flow_registry: dict[str, Callable[..., Any]],
    lease_seconds: int = 30,
) -> bool:
    claimed = control_plane.claim_next_deployment_run(
        worker_name=worker_name,
        lease_seconds=lease_seconds,
        work_pool_id=work_pool_id,
    )
    if not claimed:
        return False
    execute_claimed_deployment_run(control_plane, claimed, flow_registry)
    return True


def run_worker_loop(
    control_plane: Any,
    *,
    worker_name: str,
    work_pool_id: str,
    flow_registry: dict[str, Callable[..., Any]],
    lease_seconds: int = 30,
    stop_event: threading.Event,
    heartbeat_interval: float = 15.0,
) -> None:
    last_heartbeat = 0.0
    use_rust_wait = bool(getattr(control_plane, "_rust_db_bound", False))

    while not stop_event.is_set():
        now_m = time.monotonic()
        if now_m - last_heartbeat > heartbeat_interval:
            try:
                control_plane.worker_heartbeat(worker_name)
            except Exception:
                pass
            last_heartbeat = now_m

        if use_rust_wait:
            claimed = control_plane.claim_next_deployment_run_wait(
                worker_name=worker_name,
                lease_seconds=lease_seconds,
                wait_ms=500,
                work_pool_id=work_pool_id,
            )
            if not claimed:
                continue
            execute_claimed_deployment_run(control_plane, claimed, flow_registry)
        else:
            handled = run_local_deployment_once(
                control_plane,
                worker_name,
                work_pool_id,
                flow_registry,
                lease_seconds=lease_seconds,
            )
            if not handled:
                time.sleep(0.5)


def run_http_worker_loop(
    client: Any,
    *,
    worker_name: str,
    work_pool_id: str,
    flow_registry: dict[str, Callable[..., Any]],
    lease_seconds: int = 30,
    stop_event: threading.Event,
    heartbeat_interval: float = 15.0,
    wait_ms: int = 500,
) -> None:
    """Worker loop that never opens the control-plane database (API only)."""
    backend = HttpWorkerBackend(client)
    last_heartbeat = 0.0

    while not stop_event.is_set():
        now_m = time.monotonic()
        if now_m - last_heartbeat > heartbeat_interval:
            try:
                client.heartbeat(worker_name, work_pool_id=work_pool_id)
            except Exception:
                pass
            last_heartbeat = now_m

        try:
            claimed = client.claim(
                worker_name,
                work_pool_id=work_pool_id,
                lease_seconds=lease_seconds,
                wait_ms=wait_ms,
            )
        except Exception:
            time.sleep(0.5)
            continue
        if not claimed:
            time.sleep(0.5)
            continue
        execute_claimed_deployment_run(backend, claimed, flow_registry)
