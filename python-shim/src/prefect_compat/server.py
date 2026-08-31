"""FastAPI application: middleware, embedded worker/scheduler, route registry."""

from __future__ import annotations

import os
import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth_middleware import BasicAuthMiddleware
from .flow_registry import FLOW_REGISTRY, failing_flow, gated_flow, mapped_flow
from .plane import control_plane
from .routes.catalog import router as catalog_router
from .routes.concurrency import router as concurrency_router
from .routes.deployments import router as deployments_router
from .routes.flow_runs import router as flow_runs_router
from .routes.health import router as health_router
from .routes.streams import router as streams_router
from .routes.work_pools import router as work_pools_router
from .routes.workers import router as workers_router
from .worker import run_local_deployment_once, run_worker_loop

_worker_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None
_scheduler_stop_event = threading.Event()
_scheduler_thread: threading.Thread | None = None
_rust_scheduler_started = False
LOCAL_WORKER_NAME = os.getenv("IRONFLOW_LOCAL_WORKER_NAME", "local-worker-1")
LOCAL_WORK_POOL = os.getenv("IRONFLOW_WORK_POOL", "default-process-pool")

app = FastAPI(title="IronFlow Compat Server")
app.include_router(health_router)
app.include_router(flow_runs_router)
app.include_router(catalog_router)
app.include_router(deployments_router)
app.include_router(work_pools_router)
app.include_router(concurrency_router)
app.include_router(streams_router)
app.include_router(workers_router)
app.add_middleware(BasicAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _run_local_deployment_once(worker_name: str | None = None) -> bool:
    return run_local_deployment_once(
        control_plane,
        worker_name or LOCAL_WORKER_NAME,
        LOCAL_WORK_POOL,
        FLOW_REGISTRY,
    )


def _local_worker_loop() -> None:
    run_worker_loop(
        control_plane,
        worker_name=LOCAL_WORKER_NAME,
        work_pool_id=LOCAL_WORK_POOL,
        flow_registry=FLOW_REGISTRY,
        stop_event=_worker_stop_event,
    )


def _scheduler_maintenance_loop() -> None:
    while not _scheduler_stop_event.is_set():
        time.sleep(1.0)
        try:
            control_plane.deployment_maintenance_tick()
        except Exception:
            pass


@app.on_event("startup")
def _startup_local_worker() -> None:
    for flow_name in FLOW_REGISTRY:
        control_plane.create_deployment(
            name=f"{flow_name}-local",
            flow_name=flow_name,
            default_parameters={"n": 3},
            paused=False,
        )
    global _worker_thread, _scheduler_thread, _rust_scheduler_started
    if os.getenv("IRONFLOW_ENABLE_SCHEDULER", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }:
        interval_ms = int(os.getenv("IRONFLOW_SCHEDULER_INTERVAL_MS", "1000"))
        stale = int(os.getenv("IRONFLOW_SCHEDULER_STALE_SECONDS", "120"))
        if control_plane.start_rust_deployment_scheduler(
            interval_ms=interval_ms, stale_after_seconds=stale
        ):
            _rust_scheduler_started = True
        elif _scheduler_thread is None or not _scheduler_thread.is_alive():
            _scheduler_stop_event.clear()
            _scheduler_thread = threading.Thread(
                target=_scheduler_maintenance_loop,
                name="ironflow-scheduler",
                daemon=True,
            )
            _scheduler_thread.start()
    if os.getenv("IRONFLOW_ENABLE_LOCAL_WORKER", "1").strip().lower() in {
        "0",
        "false",
        "no",
    }:
        return
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_stop_event.clear()
        _worker_thread = threading.Thread(
            target=_local_worker_loop, name="ironflow-local-worker", daemon=True
        )
        _worker_thread.start()


@app.on_event("shutdown")
def _shutdown_local_worker() -> None:
    global _rust_scheduler_started
    _worker_stop_event.set()
    _scheduler_stop_event.set()
    if _rust_scheduler_started:
        control_plane.stop_rust_deployment_scheduler()
        _rust_scheduler_started = False


__all__ = [
    "FLOW_REGISTRY",
    "app",
    "control_plane",
    "failing_flow",
    "gated_flow",
    "mapped_flow",
]
