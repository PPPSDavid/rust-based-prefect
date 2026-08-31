"""Health, history summary, and in-process benchmark endpoints."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from ..flow_registry import BENCHMARK_FLAVOR_HELP, BENCHMARK_FLOW_MAP
from ..plane import control_plane
from .schemas import BenchmarkRequest

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/history/summary")
def history_summary() -> dict[str, int]:
    return control_plane.summary()


@router.post("/benchmark/run")
def benchmark_run(req: BenchmarkRequest) -> dict[str, float | int | str | bool | None]:
    flow_fn = BENCHMARK_FLOW_MAP.get(req.flavor)
    if flow_fn is None:
        raise HTTPException(status_code=400, detail=BENCHMARK_FLAVOR_HELP)
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
