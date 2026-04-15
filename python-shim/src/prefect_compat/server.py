from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from .decorators import flow, set_control_plane, task, wait
from .runtime import InMemoryControlPlane


class BenchmarkRequest(BaseModel):
    flavor: str
    complexity: int


class CursorPage(BaseModel):
    items: list[dict]
    next_cursor: str | None = None


history_path = os.getenv("IRONFLOW_HISTORY_PATH")
if history_path is None:
    history_path = str(Path("data") / "ironflow_history.jsonl")

control_plane = InMemoryControlPlane(history_path=history_path)
set_control_plane(control_plane)

app = FastAPI(title="IronFlow Compat Server")


@task
def inc(x: int) -> int:
    return x + 1


@task
def dbl(x: int) -> int:
    return x * 2


@task
def passthrough(x: int) -> int:
    return x


@flow
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/history/summary")
def history_summary() -> dict[str, int]:
    return control_plane.summary()


@app.post("/benchmark/run")
def benchmark_run(req: BenchmarkRequest) -> dict[str, float | int]:
    flow_fn = mapped_flow if req.flavor == "mapped" else chained_flow
    start = time.perf_counter()
    _ = flow_fn(req.complexity)
    runtime = time.perf_counter() - start
    summary = control_plane.summary()
    events = summary["events"]
    return {
        "runtime_seconds": runtime,
        "events": events,
        "transitions_per_second": (events / runtime) if runtime > 0 else 0.0,
    }


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

    await asyncio.sleep(1.0)


@app.get("/api/stream/flow-runs")
async def stream_flow_runs() -> StreamingResponse:
    return StreamingResponse(_sse_stream(channel="flow-runs"), media_type="text/event-stream")


@app.get("/api/stream/flow-runs/{flow_run_id}")
async def stream_flow_run(flow_run_id: UUID) -> StreamingResponse:
    return StreamingResponse(
        _sse_stream(channel="flow-run", run_id=flow_run_id),
        media_type="text/event-stream",
    )
