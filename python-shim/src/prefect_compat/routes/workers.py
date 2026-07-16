"""Worker heartbeat / claim / started / finished HTTP API (Tier B2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/workers", tags=["workers"])


class WorkerHeartbeatRequest(BaseModel):
    name: str
    work_pool_id: str | None = None


class WorkerClaimRequest(BaseModel):
    worker_name: str
    work_pool_id: str | None = None
    lease_seconds: int = Field(default=30, ge=1, le=3600)
    wait_ms: int | None = Field(default=None, ge=0, le=60_000)


class WorkerRunFinishedRequest(BaseModel):
    status: str
    flow_run_id: UUID | None = None
    error: str | None = None


def _enrich_claim(control_plane: Any, claimed: dict[str, Any]) -> dict[str, Any]:
    """Attach deployment execution fields needed by HTTP workers (B2.5)."""
    dep_id = claimed.get("deployment_id")
    if not dep_id:
        return claimed
    deployment = control_plane.get_deployment(UUID(str(dep_id)))
    if deployment is None:
        claimed["deployment"] = None
        return claimed
    claimed["deployment"] = {
        "id": deployment["id"],
        "name": deployment["name"],
        "flow_name": deployment["flow_name"],
        "entrypoint": deployment.get("entrypoint"),
        "path": deployment.get("path"),
    }
    return claimed


def _plane() -> Any:
    from .. import server as server_mod

    return server_mod.control_plane


@router.post("/heartbeat")
def worker_heartbeat(req: WorkerHeartbeatRequest) -> dict:
    control_plane = _plane()
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


@router.post("/claim")
def worker_claim(req: WorkerClaimRequest, response: Response) -> dict | None:
    """Claim the next SCHEDULED deployment run for this worker.

    Returns an enriched claim payload, or HTTP 204 when the queue is empty.
    Uses the same lease/concurrency rules as in-process claim (Rust when bound).
    """
    control_plane = _plane()
    wait_ms = req.wait_ms
    if wait_ms is not None and wait_ms > 0 and getattr(
        control_plane, "_rust_db_bound", False
    ):
        claimed = control_plane.claim_next_deployment_run_wait(
            worker_name=req.worker_name,
            lease_seconds=req.lease_seconds,
            wait_ms=wait_ms,
            work_pool_id=req.work_pool_id,
        )
    else:
        claimed = control_plane.claim_next_deployment_run(
            worker_name=req.worker_name,
            lease_seconds=req.lease_seconds,
            work_pool_id=req.work_pool_id,
        )
    if not claimed:
        response.status_code = 204
        return None
    return _enrich_claim(control_plane, claimed)


@router.post("/runs/{deployment_run_id}/started")
def worker_run_started(deployment_run_id: UUID) -> dict:
    control_plane = _plane()
    control_plane.mark_deployment_run_started(deployment_run_id)
    run = control_plane.get_deployment_run(deployment_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Deployment run not found")
    return run


@router.post("/runs/{deployment_run_id}/finished")
def worker_run_finished(
    deployment_run_id: UUID, req: WorkerRunFinishedRequest
) -> dict:
    control_plane = _plane()
    control_plane.mark_deployment_run_finished(
        deployment_run_id=deployment_run_id,
        status=req.status,
        flow_run_id=req.flow_run_id,
        error=req.error,
    )
    run = control_plane.get_deployment_run(deployment_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Deployment run not found")
    return run


@router.get("/runs/{deployment_run_id}")
def worker_get_deployment_run(deployment_run_id: UUID) -> dict:
    control_plane = _plane()
    run = control_plane.get_deployment_run(deployment_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Deployment run not found")
    return run
