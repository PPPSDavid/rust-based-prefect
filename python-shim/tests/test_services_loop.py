"""Tests for background services loop (Tier B3)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from prefect_compat.runtime import InMemoryControlPlane
from prefect_compat.services import run_services_loop


def test_services_loop_python_tick_then_stop(tmp_path: Path) -> None:
    history = tmp_path / "services-history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    # Force Python polling path for determinism in CI.
    plane._rust_db_bound = False
    plane._rust_fsm_bridge = None
    plane._rust_fsm_handle = None

    ticks: list[dict] = []
    original = plane.deployment_maintenance_tick

    def _counting_tick(stale_after_seconds: int = 120) -> dict:
        result = original(stale_after_seconds=stale_after_seconds)
        ticks.append(result)
        return result

    plane.deployment_maintenance_tick = _counting_tick  # type: ignore[method-assign]
    stop = threading.Event()

    def _runner() -> None:
        run_services_loop(
            plane,
            interval_ms=50,
            stale_after_seconds=30,
            stop_event=stop,
        )

    thread = threading.Thread(target=_runner, name="test-services", daemon=True)
    thread.start()
    deadline = time.time() + 2.0
    while len(ticks) < 1 and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    thread.join(timeout=2.0)
    assert len(ticks) >= 1
    assert "reclaimed" in ticks[0]


def test_cli_server_services_start_help() -> None:
    from prefect_compat.cli.main import _build_parser

    parser = _build_parser()
    args = parser.parse_args(
        ["server", "services", "start", "--interval-ms", "500", "--stale-seconds", "60"]
    )
    assert args.func.__name__ == "cmd_server_services_start"
    assert args.interval_ms == 500
    assert args.stale_seconds == 60
