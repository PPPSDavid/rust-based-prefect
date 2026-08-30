"""Global concurrency limit HTTP API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..plane import control_plane
from .schemas import ConcurrencyLimitCreateRequest, ConcurrencyLimitPatchRequest

router = APIRouter(tags=["concurrency-limits"])


@router.get("/api/concurrency-limits")
def list_concurrency_limits() -> dict:
    return {"limits": control_plane.list_concurrency_limits()}


@router.post("/api/concurrency-limits")
def create_concurrency_limit(req: ConcurrencyLimitCreateRequest) -> dict:
    return control_plane.upsert_concurrency_limit(
        name=req.name,
        limit=req.limit,
        slot_decay_per_second=req.slot_decay_per_second,
        active=req.active,
    )


@router.get("/api/concurrency-limits/{name}")
def get_concurrency_limit(name: str) -> dict:
    lim = control_plane.get_concurrency_limit(name)
    if lim is None:
        raise HTTPException(status_code=404, detail="concurrency limit not found")
    return lim


@router.patch("/api/concurrency-limits/{name}")
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
        active=req.active
        if req.active is not None
        else bool(current.get("active", True)),
    )


@router.delete("/api/concurrency-limits/{name}")
def delete_concurrency_limit(name: str) -> dict:
    return control_plane.delete_concurrency_limit(name)
