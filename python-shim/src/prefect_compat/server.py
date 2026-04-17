from __future__ import annotations

import os
import threading
import time
from importlib import import_module
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from .decorators import flow, set_control_plane, task, wait
from .runtime import InMemoryControlPlane
from .task_runners import ThreadPoolTaskRunner


class BenchmarkRequest(BaseModel):
    flavor: str
    complexity: int


class CursorPage(BaseModel):
    items: list[dict]
    next_cursor: str | None = None


class DeploymentCreateRequest(BaseModel):
    name: str
    flow_name: str
    entrypoint: str | None = None
    path: str | None = None
    default_parameters: dict = Field(default_factory=dict)
    paused: bool = False


class DeploymentRunTriggerRequest(BaseModel):
    parameters: dict | None = None
    idempotency_key: str | None = None


history_path = os.getenv("IRONFLOW_HISTORY_PATH")
if history_path is None:
    history_path = str(Path("data") / "ironflow_history.jsonl")

control_plane = InMemoryControlPlane(history_path=history_path)
set_control_plane(control_plane)
_worker_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None
_worker_last_heartbeat = 0.0
_scheduler_stop_event = threading.Event()
_scheduler_thread: threading.Thread | None = None
LOCAL_WORKER_NAME = os.getenv("IRONFLOW_LOCAL_WORKER_NAME", "local-worker-1")

app = FastAPI(title="IronFlow Compat Server")
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


@task
def inc(x: int) -> int:
    return x + 1


@task
def dbl(x: int) -> int:
    return x * 2


@task
def passthrough(x: int) -> int:
    return x


@task
def explode(_: int) -> int:
    raise RuntimeError("intentional failure for DAG/state testing")


@task
def after_failure(x: int) -> int:
    return x + 10


@flow(task_runner=ThreadPoolTaskRunner())
def mapped_flow(n: int) -> int:
    first = inc.submit(n)
    mapped_futs = dbl.map(range(n), wait_for=[first])
    wait(mapped_futs)
    return sum(f.result() for f in mapped_futs)


@flow
def chained_flow(n: int) -> int:
    f = passthrough.submit(0)
    for _ in range(n):
        f = inc.submit(f, wait_for=[f])
    return f.result()


@flow
def simple_flow(n: int) -> int:
    # Simple dependency shape: one task depends on one upstream.
    first = inc.submit(n)
    second = dbl.submit(first, wait_for=[first])
    return second.result()


@flow(task_runner=ThreadPoolTaskRunner())
def wide_flow(n: int) -> int:
    # Wide fan-out shape: one upstream gate then many independent mapped tasks.
    first = inc.submit(n)
    mapped_futs = dbl.map(range(n), wait_for=[first])
    wait(mapped_futs)
    return sum(f.result() for f in mapped_futs)


@flow
def long_chain_flow(n: int) -> int:
    # Long dependency chain: strict serial dependence across many tasks.
    f = passthrough.submit(0)
    for _ in range(n):
        f = inc.submit(f, wait_for=[f])
    return f.result()


@flow
def failing_flow(n: int) -> int:
    first = inc.submit(n)
    bad = explode.submit(first, wait_for=[first])
    # This node should be unreachable once upstream fails.
    final = after_failure.submit(bad, wait_for=[bad])
    return final.result()


FLOW_REGISTRY = {
    "simple_flow": simple_flow,
    "wide_flow": wide_flow,
    "long_chain_flow": long_chain_flow,
    "mapped_flow": mapped_flow,
    "chained_flow": chained_flow,
    "failing_flow": failing_flow,
}


def _resolve_flow_callable(flow_name: str, entrypoint: str | None = None):
    if flow_name in FLOW_REGISTRY:
        return FLOW_REGISTRY[flow_name]
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


def _run_local_deployment_once(worker_name: str | None = None) -> bool:
    wn = worker_name or LOCAL_WORKER_NAME
    claimed = control_plane.claim_next_deployment_run(worker_name=wn, lease_seconds=30)
    if not claimed:
        return False

    deployment = control_plane.get_deployment(UUID(claimed["deployment_id"]))
    if deployment is None:
        control_plane.mark_deployment_run_finished(
            deployment_run_id=UUID(claimed["id"]),
            status="FAILED",
            error="Deployment not found",
        )
        return True

    control_plane.mark_deployment_run_started(UUID(claimed["id"]))
    flow_run_id: UUID | None = None
    try:
        flow_fn = _resolve_flow_callable(deployment["flow_name"], deployment.get("entrypoint"))
        params = claimed.get("resolved_parameters", {}) or {}
        flow_fn(**params)
        latest = control_plane.latest_flow()
        if latest is not None:
            flow_run_id = latest.run_id
        control_plane.mark_deployment_run_finished(
            deployment_run_id=UUID(claimed["id"]),
            status="COMPLETED",
            flow_run_id=flow_run_id,
        )
    except Exception as exc:
        control_plane.mark_deployment_run_finished(
            deployment_run_id=UUID(claimed["id"]),
            status="FAILED",
            flow_run_id=flow_run_id,
            error=str(exc),
        )
    return True


def _local_worker_loop() -> None:
    global _worker_last_heartbeat
    while not _worker_stop_event.is_set():
        now_m = time.monotonic()
        if now_m - _worker_last_heartbeat > 15.0:
            try:
                control_plane.worker_heartbeat(LOCAL_WORKER_NAME)
            except Exception:
                pass
            _worker_last_heartbeat = now_m
        handled = _run_local_deployment_once(worker_name=LOCAL_WORKER_NAME)
        if not handled:
            time.sleep(0.5)


def _scheduler_maintenance_loop() -> None:
    while not _scheduler_stop_event.is_set():
        time.sleep(1.0)
        try:
            control_plane.deployment_maintenance_tick()
        except Exception:
            pass


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/history/summary")
def history_summary() -> dict[str, int]:
    return control_plane.summary()


@app.post("/benchmark/run")
def benchmark_run(req: BenchmarkRequest) -> dict[str, float | int | str | bool | None]:
    flow_map = {
        "simple": simple_flow,
        "wide": wide_flow,
        "long_chain": long_chain_flow,
        "failing": failing_flow,
        # Backwards-compatible aliases for existing scripts.
        "mapped": wide_flow,
        "chained": long_chain_flow,
    }
    flow_fn = flow_map.get(req.flavor)
    if flow_fn is None:
        raise HTTPException(
            status_code=400,
            detail="Unsupported flavor. Use one of: simple, wide, long_chain, failing",
        )
    start = time.perf_counter()
    error: str | None = None
    try:
        _ = flow_fn(req.complexity)
    except Exception as exc:
        error = str(exc)
    runtime = time.perf_counter() - start
    summary = control_plane.summary()
    events = summary["events"]
    payload: dict[str, float | int | str | bool | None] = {
        "runtime_seconds": runtime,
        "events": events,
        "transitions_per_second": (events / runtime) if runtime > 0 else 0.0,
    }
    if error is not None:
        payload["flow_failed"] = True
        payload["error"] = error
    return payload


@app.on_event("startup")
def _startup_local_worker() -> None:
    for flow_name in FLOW_REGISTRY:
        control_plane.create_deployment(
            name=f"{flow_name}-local",
            flow_name=flow_name,
            default_parameters={"n": 3},
            paused=False,
        )
    global _worker_thread, _scheduler_thread
    if os.getenv("IRONFLOW_ENABLE_SCHEDULER", "1").strip().lower() not in {"0", "false", "no"}:
        if _scheduler_thread is None or not _scheduler_thread.is_alive():
            _scheduler_stop_event.clear()
            _scheduler_thread = threading.Thread(
                target=_scheduler_maintenance_loop, name="ironflow-scheduler", daemon=True
            )
            _scheduler_thread.start()
    if os.getenv("IRONFLOW_ENABLE_LOCAL_WORKER", "1").strip().lower() in {"0", "false", "no"}:
        return
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_stop_event.clear()
        _worker_thread = threading.Thread(target=_local_worker_loop, name="ironflow-local-worker", daemon=True)
        _worker_thread.start()


@app.on_event("shutdown")
def _shutdown_local_worker() -> None:
    _worker_stop_event.set()
    _scheduler_stop_event.set()


@app.get("/api/flow-runs", response_model=CursorPage)
def list_flow_runs(
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_flow_runs(state=state, limit=limit, cursor=cursor)
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@app.get("/api/flow-runs/{flow_run_id}")
def get_flow_run(flow_run_id: UUID) -> dict:
    run = control_plane.get_flow_run_detail(flow_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Flow run not found")
    return run


@app.get("/api/flow-runs/{flow_run_id}/task-runs", response_model=CursorPage)
def list_task_runs(
    flow_run_id: UUID,
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_task_runs(flow_run_id=flow_run_id, limit=limit, cursor=cursor)
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@app.get("/api/flow-runs/{flow_run_id}/logs", response_model=CursorPage)
def list_logs(
    flow_run_id: UUID,
    task_run_id: UUID | None = Query(default=None),
    level: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_logs(
        flow_run_id=flow_run_id,
        task_run_id=task_run_id,
        level=level,
        limit=limit,
        cursor=cursor,
    )
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@app.get("/api/flows", response_model=CursorPage)
def list_flows(
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_flows(limit=limit, cursor=cursor)
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@app.get("/api/deployments", response_model=CursorPage)
def list_deployments(
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_deployments(limit=limit, cursor=cursor)
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@app.post("/api/deployments")
def create_deployment(req: DeploymentCreateRequest) -> dict:
    return control_plane.create_deployment(
        name=req.name,
        flow_name=req.flow_name,
        entrypoint=req.entrypoint,
        path=req.path,
        default_parameters=req.default_parameters,
        paused=req.paused,
    )


@app.get("/api/deployment-runs", response_model=CursorPage)
def list_deployment_runs(
    deployment_id: UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_deployment_runs(deployment_id=deployment_id, limit=limit, cursor=cursor)
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@app.post("/api/deployments/{deployment_id}/run")
def trigger_deployment_run(deployment_id: UUID, req: DeploymentRunTriggerRequest) -> dict:
    try:
        return control_plane.trigger_deployment_run(
            deployment_id=deployment_id,
            parameters=req.parameters,
            idempotency_key=req.idempotency_key,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.get("/api/flows/{flow_name}")
def get_flow(flow_name: str) -> dict:
    tasks = control_plane.list_tasks(flow_name=flow_name, limit=500)
    return {"name": flow_name, "tasks": tasks}


@app.get("/api/tasks")
def list_tasks(
    flow_name: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict]:
    return control_plane.list_tasks(flow_name=flow_name, limit=limit)


@app.get("/api/flow-runs/{flow_run_id}/events", response_model=CursorPage)
def list_events(
    flow_run_id: UUID,
    limit: int = Query(default=500, ge=1, le=2000),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_events(flow_run_id=flow_run_id, limit=limit, cursor=cursor)
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@app.get("/api/flow-runs/{flow_run_id}/dag")
def get_flow_run_dag(
    flow_run_id: UUID,
    mode: str = Query(default="logical"),
) -> dict:
    if mode not in {"logical", "expanded"}:
        raise HTTPException(status_code=400, detail="mode must be 'logical' or 'expanded'")
    return control_plane.get_flow_run_dag(flow_run_id=flow_run_id, mode=mode)


@app.get("/api/flow-runs/{flow_run_id}/artifacts")
def list_flow_artifacts(
    flow_run_id: UUID, limit: int = Query(default=200, ge=1, le=2000)
) -> list[dict]:
    return control_plane.list_artifacts_for_flow(flow_run_id=flow_run_id, limit=limit)


@app.get("/api/task-runs/{task_run_id}/artifacts")
def list_task_artifacts(
    task_run_id: UUID, limit: int = Query(default=200, ge=1, le=2000)
) -> list[dict]:
    return control_plane.list_artifacts_for_task(task_run_id=task_run_id, limit=limit)


@app.get("/api/artifacts/{artifact_id}")
def get_artifact(artifact_id: UUID) -> dict:
    artifact = control_plane.get_artifact(artifact_id=artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


async def _sse_stream(channel: str, run_id: UUID | None = None) -> AsyncIterator[str]:
    last_seen = None
    while True:
        if channel == "flow-runs":
            page = control_plane.list_flow_runs(limit=1)
            payload = page.items[0] if page.items else {}
        else:
            if run_id is None:
                payload = {}
            else:
                events = control_plane.list_events(flow_run_id=run_id, limit=1)
                payload = events.items[0] if events.items else {}
        key = jsonable(payload)
        if key != last_seen:
            last_seen = key
            yield f"data: {key}\n\n"
        await _sleep_short()


def jsonable(obj: dict) -> str:
    import json

    return json.dumps(obj)


async def _sleep_short() -> None:
    import asyncio

    await asyncio.sleep(0.25)


@app.get("/api/stream/flow-runs")
async def stream_flow_runs() -> StreamingResponse:
    return StreamingResponse(_sse_stream(channel="flow-runs"), media_type="text/event-stream")


@app.get("/api/stream/flow-runs/{flow_run_id}")
async def stream_flow_run(flow_run_id: UUID) -> StreamingResponse:
    return StreamingResponse(
        _sse_stream(channel="flow-run", run_id=flow_run_id),
        media_type="text/event-stream",
    )
