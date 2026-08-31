"""Static-planner forecast compilation and cache for @flow decorators."""

from __future__ import annotations

import inspect
import sys
import textwrap
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# Static planner output keyed by flow callable identity (source is stable per decorated function).
_FORECAST_BY_FLOW_FN: dict[int, dict[str, Any]] = {}


def _compile_forecast_for_flow(
    flow_fn: Callable[..., Any], flow_name: str
) -> dict[str, Any]:
    try:
        source = textwrap.dedent(inspect.getsource(flow_fn))
    except (OSError, TypeError):
        source = ""
    source_hash = hash(source)
    cache_key = id(flow_fn)
    cached = _FORECAST_BY_FLOW_FN.get(cache_key)
    if (
        cached is not None
        and cached.get("flow_name") == flow_name
        and cached.get("source_hash") == source_hash
    ):
        return cached["info"]

    info = _compile_forecast_for_flow_uncached(flow_fn, flow_name, source=source)
    _FORECAST_BY_FLOW_FN[cache_key] = {
        "flow_name": flow_name,
        "source_hash": source_hash,
        "info": info,
    }
    return info


def clear_forecast_cache() -> None:
    """Clear static-planner forecast cache (tests / long-lived processes)."""
    _FORECAST_BY_FLOW_FN.clear()


def _task_symbols_for_flow(flow_fn: Callable[..., Any]) -> dict[str, str]:
    """Map flow-local symbols to runtime task names (including @task(name=...))."""
    from .decorators import TaskWrapper

    symbols: dict[str, str] = {}
    try:
        unwrapped = inspect.unwrap(flow_fn)
        module = inspect.getmodule(unwrapped)
        namespaces: list[Mapping[str, Any]] = []
        if module is not None:
            namespaces.append(vars(module))
        closure = inspect.getclosurevars(unwrapped)
        if closure.globals:
            namespaces.append(closure.globals)
        if closure.nonlocals:
            namespaces.append(closure.nonlocals)
        for namespace in namespaces:
            for key, value in namespace.items():
                if isinstance(value, TaskWrapper):
                    symbols[key] = value.name
    except Exception:
        return symbols
    return symbols


def _compile_forecast_for_flow_uncached(
    flow_fn: Callable[..., Any], flow_name: str, *, source: str | None = None
) -> dict[str, Any]:
    try:
        from static_planner import compile_and_forecast
    except Exception:
        planner_src = Path(__file__).resolve().parents[3] / "static-planner" / "src"
        if planner_src.exists() and str(planner_src) not in sys.path:
            sys.path.append(str(planner_src))
        try:
            from static_planner import compile_and_forecast
        except Exception:
            return {
                "manifest": {},
                "forecast": {},
                "warnings": [
                    "Static planner not available; runtime fallback DAG will be used."
                ],
                "fallback_required": True,
                "source": "runtime",
            }

    try:
        if source is None:
            source = textwrap.dedent(inspect.getsource(flow_fn))
        task_names = _task_symbols_for_flow(flow_fn)
        result = compile_and_forecast(
            source, flow_name=flow_name, task_names=task_names
        )
        diagnostics = result.get("diagnostics", {})
        return {
            "manifest": result.get("manifest", {}),
            "forecast": result.get("forecast", {}),
            "warnings": diagnostics.get("warnings", []),
            "fallback_required": bool(diagnostics.get("fallback_required", False)),
            "source": "forecast",
        }
    except Exception as exc:
        return {
            "manifest": {},
            "forecast": {},
            "warnings": [f"Forecast compile failed: {exc}"],
            "fallback_required": True,
            "source": "runtime",
        }
