"""Flow-run query and lifecycle HTTP API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query

from ..lifecycle import parse_cancel_mode
from ..plane import control_plane
from .schemas import CursorPage, FlowRunCancelRequest, FlowRunPauseRequest

router = APIRouter(tags=["flow-runs"])


@router.get("/api/flow-runs", response_model=CursorPage)
def list_flow_runs(
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_flow_runs(state=state, limit=limit, cursor=cursor)
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@router.get("/api/flow-runs/{flow_run_id}")
def get_flow_run(flow_run_id: UUID) -> dict:
    run = control_plane.get_flow_run_detail(flow_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Flow run not found")
    return run


@router.get("/api/flow-runs/{flow_run_id}/task-runs", response_model=CursorPage)
def list_task_runs(
    flow_run_id: UUID,
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_task_runs(
        flow_run_id=flow_run_id, limit=limit, cursor=cursor
    )
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@router.get("/api/flow-runs/{flow_run_id}/logs", response_model=CursorPage)
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


@router.post("/api/flow-runs/{flow_run_id}/cancel")
def cancel_flow_run(
    flow_run_id: UUID,
    req: FlowRunCancelRequest | None = Body(default=None),
) -> dict:
    try:
        parse_cancel_mode(None if req is None else req.mode)
        return control_plane.cancel_flow_run(flow_run_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        if "mode" in detail:
            status_code = 422
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/api/flow-runs/{flow_run_id}/pause")
def pause_flow_run(flow_run_id: UUID, req: FlowRunPauseRequest) -> dict:
    try:
        return control_plane.pause_flow_run(flow_run_id, mode=req.mode)
    except ValueError as exc:
        detail = str(exc)
        if "mode" in detail:
            raise HTTPException(status_code=422, detail=detail) from exc
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/api/flow-runs/{flow_run_id}/resume")
def resume_flow_run(flow_run_id: UUID) -> dict:
    try:
        return control_plane.resume_flow_run(flow_run_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/api/flow-runs/{flow_run_id}/retry")
def retry_flow_run(flow_run_id: UUID) -> dict:
    try:
        return control_plane.retry_flow_run(flow_run_id)
    except ValueError as exc:
        detail = str(exc)
        if "not deployment-backed" in detail:
            raise HTTPException(status_code=409, detail=detail) from exc
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/api/flow-runs/{flow_run_id}/events", response_model=CursorPage)
def list_events(
    flow_run_id: UUID,
    limit: int = Query(default=500, ge=1, le=2000),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_events(
        flow_run_id=flow_run_id, limit=limit, cursor=cursor
    )
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@router.get("/api/flow-runs/{flow_run_id}/dag")
def get_flow_run_dag(
    flow_run_id: UUID,
    mode: str = Query(default="logical"),
) -> dict:
    if mode not in {"logical", "expanded"}:
        raise HTTPException(
            status_code=400, detail="mode must be 'logical' or 'expanded'"
        )
    return control_plane.get_flow_run_dag(flow_run_id=flow_run_id, mode=mode)
