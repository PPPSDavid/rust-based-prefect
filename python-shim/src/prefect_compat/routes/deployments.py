"""Deployment CRUD, run listing, and trigger HTTP API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from ..plane import control_plane
from .schemas import (
    CursorPage,
    DeploymentCreateRequest,
    DeploymentPatchRequest,
    DeploymentRunTriggerRequest,
)

router = APIRouter(tags=["deployments"])


@router.get("/api/deployments", response_model=CursorPage)
def list_deployments(
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_deployments(limit=limit, cursor=cursor)
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@router.post("/api/deployments")
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


@router.get("/api/deployments/by-name/{name}")
def get_deployment_by_name(name: str) -> dict:
    deployment = control_plane.get_deployment_by_name(name)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return deployment


@router.patch("/api/deployments/{deployment_id}")
def patch_deployment(deployment_id: UUID, req: DeploymentPatchRequest) -> dict:
    patch = req.model_dump(exclude_unset=True)
    try:
        return control_plane.update_deployment(deployment_id, patch)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/api/deployment-runs", response_model=CursorPage)
def list_deployment_runs(
    deployment_id: UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_deployment_runs(
        deployment_id=deployment_id, limit=limit, cursor=cursor
    )
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@router.get("/api/deployments/{deployment_id}")
def get_deployment(deployment_id: UUID) -> dict:
    deployment = control_plane.get_deployment(deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return deployment


@router.post("/api/deployments/{deployment_id}/run")
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
