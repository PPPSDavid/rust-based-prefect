"""Server-sent event streams for flow-run updates."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from ..plane import control_plane

router = APIRouter(tags=["streams"])


def jsonable(obj: dict) -> str:
    return json.dumps(obj)


async def _sse_stream(channel: str, run_id: UUID | None = None) -> AsyncIterator[str]:
    last_seen = None
    while True:
        if channel == "flow-runs":
            page = control_plane.list_flow_runs(limit=1)
            payload = page.items[0] if page.items else {}
        else:
            if run_id is None:
                payload = {}
            else:
                events = control_plane.list_events(flow_run_id=run_id, limit=1)
                payload = events.items[0] if events.items else {}
        key = jsonable(payload)
        if key != last_seen:
            last_seen = key
            yield f"data: {key}\n\n"
        await asyncio.sleep(0.25)


@router.get("/api/stream/flow-runs")
async def stream_flow_runs() -> StreamingResponse:
    return StreamingResponse(
        _sse_stream(channel="flow-runs"), media_type="text/event-stream"
    )


@router.get("/api/stream/flow-runs/{flow_run_id}")
async def stream_flow_run(flow_run_id: UUID) -> StreamingResponse:
    return StreamingResponse(
        _sse_stream(channel="flow-run", run_id=flow_run_id),
        media_type="text/event-stream",
    )
