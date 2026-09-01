"""Demo flow registry lives outside the FastAPI module."""

from __future__ import annotations

from pathlib import Path

from prefect_compat.flow_registry import BENCHMARK_FLOW_MAP, FLOW_REGISTRY
from prefect_compat.worker import resolve_flow_callable

WORKER_SRC = (
    Path(__file__).resolve().parents[1] / "src" / "prefect_compat" / "worker.py"
)


def test_registry_includes_demo_flows() -> None:
    assert "simple_flow" in FLOW_REGISTRY
    assert "failing_flow" in FLOW_REGISTRY
    assert "persist_result_demo" in FLOW_REGISTRY
    assert "cancelable_flow" in FLOW_REGISTRY
    assert "slow_wide_flow" in FLOW_REGISTRY
    assert "slow_wide_flow" not in BENCHMARK_FLOW_MAP


def test_resolve_flow_callable_default_registry() -> None:
    fn = resolve_flow_callable("simple_flow")
    assert getattr(fn, "__name__", "") == "simple_flow"


def test_worker_does_not_import_fastapi_server() -> None:
    text = WORKER_SRC.read_text(encoding="utf-8")
    assert "from .server import" not in text
    assert "prefect_compat.server" not in text
