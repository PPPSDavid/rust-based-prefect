"""Paginated flow-run search. Kept off the lifecycle route module."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..control_plane.flow_run_list import FLOW_RUN_STATES
from ..plane import control_plane
from .schemas import CursorPage

router = APIRouter(tags=["flow-runs"])


@router.get("/api/flow-runs", response_model=CursorPage)
def list_flow_runs(
    state: str | None = Query(default=None),
    q: str | None = Query(default=None),
    created_after: str | None = Query(default=None),
    created_before: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
) -> CursorPage:
    cleaned_state = (state or "").strip() or None
    if cleaned_state is not None and cleaned_state not in FLOW_RUN_STATES:
        allowed = ", ".join(sorted(FLOW_RUN_STATES))
        raise HTTPException(status_code=400, detail=f"state must be one of: {allowed}")
    try:
        page = control_plane.list_flow_runs(
            state=cleaned_state,
            limit=limit,
            cursor=cursor,
            include_archived=include_archived,
            q=q,
            created_after=created_after,
            created_before=created_before,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CursorPage(items=page.items, next_cursor=page.next_cursor)
