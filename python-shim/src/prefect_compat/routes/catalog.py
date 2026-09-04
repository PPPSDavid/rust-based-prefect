"""Flow catalog, task lists, artifacts, and catalog lifecycle HTTP API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from ..errors import FlowCatalogConflict
from ..flow_catalog_settings import catalog_server_info
from ..plane import control_plane
from .schemas import (
    CursorPage,
    DeploymentsApplyRequest,
    FlowRenameRequest,
)

router = APIRouter(tags=["catalog"])


def _conflict(exc: FlowCatalogConflict) -> HTTPException:
    return HTTPException(status_code=409, detail=exc.http_detail())


@router.get("/api/server-info")
def server_info() -> dict[str, bool | int | str]:
    payload: dict[str, bool | int | str] = {"status": "ok"}
    payload.update(catalog_server_info())
    return payload


@router.get("/api/flows", response_model=CursorPage)
def list_flows(
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_flows(limit=limit, cursor=cursor, status=status)
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@router.get("/api/flows/{flow_name}")
def get_flow(flow_name: str) -> dict:
    detail = control_plane.get_flow_catalog_detail(flow_name)
    if detail is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return detail


@router.post("/api/flows/{flow_id}/rename")
def rename_flow(flow_id: str, req: FlowRenameRequest) -> dict:
    try:
        return control_plane.rename_flow(flow_id, req.name)
    except FlowCatalogConflict as exc:
        raise _conflict(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/flows/{flow_id}/archive")
def archive_flow(flow_id: str) -> dict:
    try:
        return control_plane.archive_flow(flow_id)
    except FlowCatalogConflict as exc:
        raise _conflict(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/flows/{flow_id}/restore")
def restore_flow(flow_id: str) -> dict:
    try:
        return control_plane.restore_flow(flow_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/flows/{flow_id}")
def delete_flow(flow_id: str) -> dict:
    try:
        return control_plane.delete_flow(flow_id)
    except FlowCatalogConflict as exc:
        raise _conflict(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/deployments/apply")
def apply_deployments(req: DeploymentsApplyRequest) -> dict:
    try:
        return control_plane.apply_deployments(req.deployments, prune=req.prune)
    except FlowCatalogConflict as exc:
        raise _conflict(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/tasks")
def list_tasks(
    flow_name: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict]:
    return control_plane.list_tasks(flow_name=flow_name, limit=limit)


@router.get("/api/flow-runs/{flow_run_id}/artifacts")
def list_flow_artifacts(
    flow_run_id: UUID, limit: int = Query(default=200, ge=1, le=2000)
) -> list[dict]:
    return control_plane.list_artifacts_for_flow(flow_run_id=flow_run_id, limit=limit)


@router.get("/api/task-runs/{task_run_id}/artifacts")
def list_task_artifacts(
    task_run_id: UUID, limit: int = Query(default=200, ge=1, le=2000)
) -> list[dict]:
    return control_plane.list_artifacts_for_task(task_run_id=task_run_id, limit=limit)


@router.get("/api/artifacts/{artifact_id}")
def get_artifact(artifact_id: UUID) -> dict:
    artifact = control_plane.get_artifact(artifact_id=artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact
