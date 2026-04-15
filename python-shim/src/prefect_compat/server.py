from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from .decorators import flow, set_control_plane, task, wait
from .runtime import InMemoryControlPlane


class BenchmarkRequest(BaseModel):
    flavor: str
    complexity: int


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
