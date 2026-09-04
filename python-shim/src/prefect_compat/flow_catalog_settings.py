"""Server flags for flow catalog visibility, run TTL, and orphan GC."""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def catalog_hide_archived() -> bool:
    return _env_bool("IRONFLOW_CATALOG_HIDE_ARCHIVED", True)


def run_retention_days() -> int:
    raw = os.getenv("IRONFLOW_RUN_RETENTION_DAYS", "90")
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        return 90


def orphan_flow_gc_enabled() -> bool:
    return _env_bool("IRONFLOW_ORPHAN_FLOW_GC", True)


def catalog_server_info() -> dict[str, bool | int]:
    return {
        "catalog_hide_archived": catalog_hide_archived(),
        "run_retention_days": run_retention_days(),
        "orphan_flow_gc": orphan_flow_gc_enabled(),
    }
