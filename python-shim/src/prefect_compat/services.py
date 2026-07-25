"""Background services process (Tier B3): scheduler / maintenance without HTTP."""

from __future__ import annotations

import os
import threading
from typing import Any


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return int(raw)


def run_services_loop(
    control_plane: Any,
    *,
    interval_ms: int | None = None,
    stale_after_seconds: int | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Run deployment maintenance ticks until ``stop_event`` is set.

    Prefers the Rust background scheduler when ``bind_db`` is available;
    otherwise polls ``deployment_maintenance_tick`` on an interval.

    Multi-replica leader election (Postgres advisory lock) is deferred —
    run a single services container per stack.
    """
    if stop_event is None:
        stop_event = threading.Event()
    interval = (
        interval_ms
        if interval_ms is not None
        else _env_int("FLOWOXIDE_SCHEDULER_INTERVAL_MS", 1000)
    )
    stale = (
        stale_after_seconds
        if stale_after_seconds is not None
        else _env_int("FLOWOXIDE_SCHEDULER_STALE_SECONDS", 120)
    )
    interval = max(1, interval)
    stale = max(1, stale)

    if control_plane.start_rust_deployment_scheduler(
        interval_ms=interval, stale_after_seconds=stale
    ):
        while not stop_event.is_set():
            stop_event.wait(timeout=1.0)
        try:
            control_plane.stop_rust_deployment_scheduler()
        except Exception:
            pass
        return

    sleep_s = interval / 1000.0
    while not stop_event.is_set():
        try:
            control_plane.deployment_maintenance_tick(stale_after_seconds=stale)
        except Exception:
            pass
        stop_event.wait(timeout=sleep_s)
