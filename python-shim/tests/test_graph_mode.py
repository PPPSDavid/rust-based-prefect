"""Tests for graph_mode resolution and resume policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task
from prefect_compat.graph_mode import (
    StaticGraphDeclarationError,
    manifest_fingerprint,
    normalize_declared_graph_mode,
    resolve_graph_mode,
)


def test_normalize_declared_graph_mode_defaults_auto() -> None:
    assert normalize_declared_graph_mode(None) == "auto"
    assert normalize_declared_graph_mode("static") == "static"


def test_resolve_auto_static_when_manifest_clean() -> None:
    manifest = {
        "nodes": [
            {"node_id": "n1", "task_name": "a", "deps": []},
            {"node_id": "n2", "task_name": "b", "deps": ["n1"]},
        ]
    }
    res = resolve_graph_mode("auto", fallback_required=False, manifest=manifest)
    assert res.effective == "static"
    assert res.manifest_fingerprint == manifest_fingerprint(manifest)


def test_resolve_auto_dynamic_when_fallback() -> None:
    res = resolve_graph_mode("auto", fallback_required=True, manifest={"nodes": []})
    assert res.effective == "dynamic"


def test_declared_static_fails_when_fallback() -> None:
    with pytest.raises(StaticGraphDeclarationError):
        resolve_graph_mode("static", fallback_required=True, manifest={"nodes": []})


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    history = tmp_path / "history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    set_control_plane(plane)
    return plane


@task
def setup() -> None:
    return None


@task(persist_result=True)
def work(x: int) -> int:
    return x * 2


def test_forced_dynamic_never_skips(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    calls = {"work": 0}

    @task(persist_result=True)
    def counted(x: int) -> int:
        calls["work"] += 1
        return x

    @flow(graph_mode="dynamic")
    def pipeline(x: int = 1) -> int:
        setup.submit()
        return counted.submit(x).result()

    pipeline(x=1)
    assert calls["work"] == 1
    rec = plane.get_flow(plane._latest_flow_run_id)  # type: ignore[arg-type]
    assert rec.effective_graph_mode == "dynamic"
    plane.prepare_resume(rec.run_id)
    pipeline(x=1)
    assert calls["work"] == 2


def test_auto_static_flow_gets_effective_static(tmp_path: Path) -> None:
    plane = _plane(tmp_path)

    @flow()
    def pipeline(x: int = 1) -> int:
        setup.submit()
        return work.submit(x).result()

    pipeline(x=1)
    latest = plane.latest_flow()
    assert latest is not None
    rec = plane.get_flow(latest.run_id)
    assert rec.effective_graph_mode == "static"
    assert rec.declared_graph_mode == "auto"
