from __future__ import annotations

import os
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from datetime import timedelta

from .auth_middleware import BasicAuthMiddleware
from .decorators import flow, set_control_plane, task, wait
from .gates import gate
from .runtime import InMemoryControlPlane
from .worker import run_local_deployment_once, run_worker_loop
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
    concurrency_limit: int | None = None
    collision_strategy: str = "ENQUEUE"
    schedule_interval_seconds: int | None = None
    schedule_cron: str | None = None
    schedule_rrule: str | None = None
    schedule_next_run_at: str | None = None
    schedule_enabled: bool = False
    work_pool_id: str | None = None


class DeploymentPatchRequest(BaseModel):
    entrypoint: str | None = None
    path: str | None = None
    default_parameters: dict[str, object] | None = None
    paused: bool | None = None
    concurrency_limit: int | None = None
    collision_strategy: str | None = None
    schedule_interval_seconds: int | None = None
    schedule_cron: str | None = None
    schedule_rrule: str | None = None
    schedule_next_run_at: str | None = None
    schedule_enabled: bool | None = None
    work_pool_id: str | None = None


class WorkPoolCreateRequest(BaseModel):
    name: str
    type: str = "process"


class WorkPoolPatchRequest(BaseModel):
    paused: bool | None = None


class WorkerHeartbeatRequest(BaseModel):
    name: str
    work_pool_id: str | None = None


class ConcurrencyLimitCreateRequest(BaseModel):
    name: str
    limit: int = Field(ge=0)
    slot_decay_per_second: float | None = Field(default=None, gt=0)
    active: bool = True


class ConcurrencyLimitPatchRequest(BaseModel):
    limit: int | None = Field(default=None, ge=0)
    slot_decay_per_second: float | None = Field(default=None, gt=0)
    active: bool | None = None


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
_scheduler_stop_event = threading.Event()
_scheduler_thread: threading.Thread | None = None
_rust_scheduler_started = False
LOCAL_WORKER_NAME = os.getenv("IRONFLOW_LOCAL_WORKER_NAME", "local-worker-1")
LOCAL_WORK_POOL = os.getenv("IRONFLOW_WORK_POOL", "default-process-pool")

app = FastAPI(title="IronFlow Compat Server")
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


@task
def sleep_seconds(seconds: float) -> None:
    from .cancellation import sleep_cancelable

    sleep_cancelable(seconds)


@flow
def gated_flow(n: int) -> int:
    """Demo flow with a temporal gate between prep and downstream work."""
    first = inc.submit(n)
    gf = gate(name="demo-gate").submit(after=timedelta(seconds=0), wait_for=[first])
    return dbl.submit(first, wait_for=[gf]).result()


@flow
def cancelable_flow(n: int, sleep_duration: float = 10.0) -> int:
    """Multi-task flow for cancel/retry UI tests: fast task, long sleep, downstream task."""
    first = inc.submit(n)
    slept = sleep_seconds.submit(sleep_duration, wait_for=[first])
    second = dbl.submit(first, wait_for=[slept])
    return second.result()


@flow
def failing_flow(n: int) -> int:
    first = inc.submit(n)
    bad = explode.submit(first, wait_for=[first])
    # This node should be unreachable once upstream fails.
    final = after_failure.submit(bad, wait_for=[bad])
    return final.result()


@task
def setup() -> None:
    return None


@task(persist_result=True)
def expensive(x: int) -> dict:
    return {"x": x, "n": 42, "items": [1, 2, 3]}


@task
def volatile(x: int) -> int:
    return x + 1


@flow(name="persist_result_demo")
def persist_result_demo_flow(n: int = 7) -> int:
    """Seed flow for UI e2e: None marker + JSON-safe persist_result payload."""
    setup.submit()
    payload = expensive.submit(n)
    return volatile.submit(payload.result()["n"]).result()


FLOW_REGISTRY = {
    "simple_flow": simple_flow,
    "wide_flow": wide_flow,
    "long_chain_flow": long_chain_flow,
    "mapped_flow": mapped_flow,
    "chained_flow": chained_flow,
    "failing_flow": failing_flow,
    "cancelable_flow": cancelable_flow,
    "gated_flow": gated_flow,
    "persist_result_demo": persist_result_demo_flow,
}


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


def _local_worker_loop_rust_wait() -> None:
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
        "gated": gated_flow,
        "persist_result": persist_result_demo_flow,
        # Backwards-compatible aliases for existing scripts.
        "mapped": wide_flow,
        "chained": long_chain_flow,
    }
    flow_fn = flow_map.get(req.flavor)
    if flow_fn is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported flavor. Use one of: simple, wide, long_chain, "
                "failing, gated, persist_result"
            ),
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
        use_rust_wait = bool(
            getattr(control_plane, "_rust_db_bound", False)
            and getattr(control_plane, "_rust_fsm_bridge", None)
        )
        target = _local_worker_loop_rust_wait if use_rust_wait else _local_worker_loop
        _worker_thread = threading.Thread(
            target=target, name="ironflow-local-worker", daemon=True
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
    page = control_plane.list_task_runs(
        flow_run_id=flow_run_id, limit=limit, cursor=cursor
    )
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


@app.get("/api/concurrency-limits")
def list_concurrency_limits() -> dict:
    return {"limits": control_plane.list_concurrency_limits()}


@app.post("/api/concurrency-limits")
def create_concurrency_limit(req: ConcurrencyLimitCreateRequest) -> dict:
    return control_plane.upsert_concurrency_limit(
        name=req.name,
        limit=req.limit,
        slot_decay_per_second=req.slot_decay_per_second,
        active=req.active,
    )


@app.get("/api/concurrency-limits/{name}")
def get_concurrency_limit(name: str) -> dict:
    lim = control_plane.get_concurrency_limit(name)
    if lim is None:
        raise HTTPException(status_code=404, detail="concurrency limit not found")
    return lim


@app.patch("/api/concurrency-limits/{name}")
def patch_concurrency_limit(name: str, req: ConcurrencyLimitPatchRequest) -> dict:
    current = control_plane.get_concurrency_limit(name)
    if current is None:
        raise HTTPException(status_code=404, detail="concurrency limit not found")
    return control_plane.upsert_concurrency_limit(
        name=name,
        limit=req.limit if req.limit is not None else int(current["limit"]),
        slot_decay_per_second=(
            req.slot_decay_per_second
            if req.slot_decay_per_second is not None
            else current.get("slot_decay_per_second")
        ),
        active=req.active if req.active is not None else bool(current.get("active", True)),
    )


@app.delete("/api/concurrency-limits/{name}")
def delete_concurrency_limit(name: str) -> dict:
    return control_plane.delete_concurrency_limit(name)


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
        concurrency_limit=req.concurrency_limit,
        collision_strategy=req.collision_strategy,
        schedule_interval_seconds=req.schedule_interval_seconds,
        schedule_cron=req.schedule_cron,
        schedule_rrule=req.schedule_rrule,
        schedule_next_run_at=req.schedule_next_run_at,
        schedule_enabled=req.schedule_enabled,
        work_pool_id=req.work_pool_id,
    )


@app.get("/api/deployments/by-name/{name}")
def get_deployment_by_name(name: str) -> dict:
    deployment = control_plane.get_deployment_by_name(name)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return deployment


@app.patch("/api/deployments/{deployment_id}")
def patch_deployment(deployment_id: UUID, req: DeploymentPatchRequest) -> dict:
    patch = req.model_dump(exclude_unset=True)
    try:
        return control_plane.update_deployment(deployment_id, patch)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.get("/api/deployment-runs", response_model=CursorPage)
def list_deployment_runs(
    deployment_id: UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_deployment_runs(
        deployment_id=deployment_id, limit=limit, cursor=cursor
    )
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@app.get("/api/deployments/{deployment_id}")
def get_deployment(deployment_id: UUID) -> dict:
    deployment = control_plane.get_deployment(deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return deployment


@app.post("/api/flow-runs/{flow_run_id}/cancel")
def cancel_flow_run(flow_run_id: UUID) -> dict:
    try:
        return control_plane.cancel_flow_run(flow_run_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post("/api/flow-runs/{flow_run_id}/retry")
def retry_flow_run(flow_run_id: UUID) -> dict:
    try:
        return control_plane.retry_flow_run(flow_run_id)
    except ValueError as exc:
        detail = str(exc)
        if "not deployment-backed" in detail:
            raise HTTPException(status_code=409, detail=detail) from exc
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.get("/api/work-pools", response_model=CursorPage)
def list_work_pools(
    limit: int = Query(default=50, ge=1, le=500),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_work_pools(limit=limit, cursor=cursor)
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@app.post("/api/work-pools")
def create_work_pool(req: WorkPoolCreateRequest) -> dict:
    try:
        return control_plane.create_work_pool(name=req.name, pool_type=req.type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/work-pools/{work_pool_id}")
def get_work_pool(work_pool_id: str) -> dict:
    pool = control_plane.get_work_pool(work_pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="Work pool not found")
    return pool


@app.patch("/api/work-pools/{work_pool_id}")
def patch_work_pool(work_pool_id: str, req: WorkPoolPatchRequest) -> dict:
    try:
        return control_plane.patch_work_pool(
            work_pool_id, req.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.get("/api/workers", response_model=CursorPage)
def list_workers(
    work_pool_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_workers(
        work_pool_id=work_pool_id, limit=limit, cursor=cursor
    )
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@app.post("/api/workers/heartbeat")
def worker_heartbeat(req: WorkerHeartbeatRequest) -> dict:
    control_plane.worker_heartbeat(req.name, work_pool_id=req.work_pool_id)
    page = control_plane.list_workers(limit=500)
    for item in page.items:
        if item["name"] == req.name:
            return item
    rows = control_plane._query_rows(
        "SELECT name, last_heartbeat, status, updated_at, work_pool_id FROM workers WHERE name = ? LIMIT 1",
        [req.name],
    )
    if not rows:
        raise HTTPException(status_code=500, detail="worker heartbeat failed")
    return control_plane._worker_row_to_dict(rows[0])


@app.post("/api/deployments/{deployment_id}/run")
def trigger_deployment_run(
    deployment_id: UUID, req: DeploymentRunTriggerRequest
) -> dict:
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
    page = control_plane.list_events(
        flow_run_id=flow_run_id, limit=limit, cursor=cursor
    )
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@app.get("/api/flow-runs/{flow_run_id}/dag")
def get_flow_run_dag(
    flow_run_id: UUID,
    mode: str = Query(default="logical"),
) -> dict:
    if mode not in {"logical", "expanded"}:
        raise HTTPException(
            status_code=400, detail="mode must be 'logical' or 'expanded'"
        )
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
    return StreamingResponse(
        _sse_stream(channel="flow-runs"), media_type="text/event-stream"
    )


@app.get("/api/stream/flow-runs/{flow_run_id}")
async def stream_flow_run(flow_run_id: UUID) -> StreamingResponse:
    return StreamingResponse(
        _sse_stream(channel="flow-run", run_id=flow_run_id),
        media_type="text/event-stream",
    )
