from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.perf_matrix import (
    canonical_matrix_compare_key,
    compare_runs,
    load_matrix_run_json,
    parse_recipe_list,
    parse_thresholds,
)


def _payload(recipe: str, flow_p95: float, throughput: float, wall_p95: float) -> dict:
    return {
        "aggregates": [
            {
                "recipe": recipe,
                "repetitions": 3,
                "params": {},
                "wall_clock_seconds": {"median": wall_p95 * 0.9, "p95": wall_p95, "p99": wall_p95 * 1.1},
                "throughput": {
                    "transitions_per_sec": {"median": throughput, "p95": throughput * 0.95, "p99": throughput * 0.9}
                },
                "latency_ms": {
                    "create_flow": {"p50": flow_p95 * 0.7, "p95": flow_p95, "p99": flow_p95 * 1.2},
                    "set_flow_state": {"p50": 1.0, "p95": 2.0, "p99": 3.0},
                },
                "process": {"cpu_seconds_used": {"median": 1.0, "p95": 1.2}},
                "sqlite": {"db_bytes_growth": {"median": 100.0, "p95": 120.0}},
                "notes": [],
            }
        ]
    }


def test_parse_recipe_list() -> None:
    assert parse_recipe_list("a,b, c ,,") == ["a", "b", "c"]


def test_parse_thresholds() -> None:
    parsed = parse_thresholds("latency_ms.create_flow.p95=0.10,throughput.transitions_per_sec.median=0.15")
    assert parsed["latency_ms.create_flow.p95"] == 0.10
    assert parsed["throughput.transitions_per_sec.median"] == 0.15


def test_compare_detects_regression() -> None:
    baseline = _payload("r1", flow_p95=10.0, throughput=100.0, wall_p95=5.0)
    candidate = _payload("r1", flow_p95=12.0, throughput=92.0, wall_p95=5.2)
    result = compare_runs(
        baseline,
        candidate,
        {
            "latency_ms.create_flow.p95": 0.10,
            "throughput.transitions_per_sec.median": 0.05,
            "wall_clock_seconds.p95": 0.10,
        },
    )
    assert result.get("compatible") is True
    assert result.get("compare_skipped") is False
    assert result["pass"] is False
    metrics = {row["metric"] for row in result["regressions"]}
    assert "latency_ms.create_flow.p95" in metrics
    assert "throughput.transitions_per_sec.median" in metrics
    assert "wall_clock_seconds.p95" not in metrics


def test_compare_skips_incompatible_benchmark_mode() -> None:
    baseline = {
        "metadata": {"matrix_compare_key": "preset:lite"},
        "recipes": ["small_narrow_few_write_cold", "medium_narrow_heavy_mixed_warm"],
        "aggregates": [],
    }
    candidate = {
        "metadata": {"matrix_compare_key": "preset:full"},
        "recipes": [],
        "aggregates": [],
    }
    result = compare_runs(baseline, candidate, {})
    assert result["compare_skipped"] is True
    assert result["compatible"] is False
    assert result["comparisons"] == []
    assert "Benchmark mode mismatch" in result["reason"]
    assert "preset:lite" in result["reason"]


def test_canonical_key_matches_preset_when_recipes_align() -> None:
    assert canonical_matrix_compare_key(
        ["medium_narrow_heavy_mixed_warm", "small_narrow_few_write_cold"]
    ) == "preset:lite"


def test_load_matrix_run_json_rejects_array(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"engine": "x"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON array"):
        load_matrix_run_json(bad)

