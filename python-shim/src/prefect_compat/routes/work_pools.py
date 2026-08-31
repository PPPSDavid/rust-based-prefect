"""Work-pool and worker listing HTTP API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..plane import control_plane
from .schemas import CursorPage, WorkPoolCreateRequest, WorkPoolPatchRequest

router = APIRouter(tags=["work-pools"])


@router.get("/api/work-pools", response_model=CursorPage)
def list_work_pools(
    limit: int = Query(default=50, ge=1, le=500),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_work_pools(limit=limit, cursor=cursor)
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@router.post("/api/work-pools")
def create_work_pool(req: WorkPoolCreateRequest) -> dict:
    try:
        return control_plane.create_work_pool(name=req.name, pool_type=req.type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/work-pools/{work_pool_id}")
def get_work_pool(work_pool_id: str) -> dict:
    pool = control_plane.get_work_pool(work_pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="Work pool not found")
    return pool


@router.patch("/api/work-pools/{work_pool_id}")
def patch_work_pool(work_pool_id: str, req: WorkPoolPatchRequest) -> dict:
    try:
        return control_plane.patch_work_pool(
            work_pool_id, req.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/api/workers", response_model=CursorPage)
def list_workers(
    work_pool_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_workers(
        work_pool_id=work_pool_id, limit=limit, cursor=cursor
    )
    return CursorPage(items=page.items, next_cursor=page.next_cursor)
