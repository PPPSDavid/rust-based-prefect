"""Flow catalog, task lists, and artifact HTTP API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from ..plane import control_plane
from .schemas import CursorPage

router = APIRouter(tags=["catalog"])


@router.get("/api/flows", response_model=CursorPage)
def list_flows(
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = Query(default=None),
) -> CursorPage:
    page = control_plane.list_flows(limit=limit, cursor=cursor)
    return CursorPage(items=page.items, next_cursor=page.next_cursor)


@router.get("/api/flows/{flow_name}")
def get_flow(flow_name: str) -> dict:
    tasks = control_plane.list_tasks(flow_name=flow_name, limit=500)
    return {"name": flow_name, "tasks": tasks}


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
