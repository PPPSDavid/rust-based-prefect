"""CPU-bound decorator microbench (GIL vs process pool vs free-threaded)."""

from __future__ import annotations

import sys

import pytest

from benchmarks.perf_matrix import (
    WorkloadRecipe,
    _presets,
    _recipe_catalog,
    _run_recipe_iteration,
    canonical_matrix_compare_key,
)


def test_cpu_task_preset_recipes_exist() -> None:
    catalog = _recipe_catalog()
    names = _presets()["cpu_task"]
    assert names == [
        "micro_map_cpu_threadpool",
        "micro_map_cpu_processpool",
        "micro_submit_cpu_threadpool",
    ]
    for name in names:
        recipe = catalog[name]
        assert recipe.cpu_bound is True
    assert canonical_matrix_compare_key(names) == "preset:cpu_task"


def test_cpu_map_threadpool_smoke() -> None:
    recipe = WorkloadRecipe(
        name="unit_cpu_map_thread",
        flow_count=1,
        tasks_per_flow=0,
        task_events_per_task=0,
        read_ratio=0.0,
        mixed=False,
        cold_start=True,
        sqlite_enabled=True,
        decorator_map_width=4,
        cpu_bound=True,
        map_runner="thread",
    )
    sample = _run_recipe_iteration(recipe, seed=42, warmup=False)
    assert sample.wall_clock_seconds > 0.0
    assert "decorator_map_micro.invocation_ms" in sample.latency_ms


@pytest.mark.skipif(
    sys.platform == "win32", reason="process pool unreliable under pytest on Windows"
)
def test_cpu_map_processpool_smoke() -> None:
    recipe = WorkloadRecipe(
        name="unit_cpu_map_process",
        flow_count=1,
        tasks_per_flow=0,
        task_events_per_task=0,
        read_ratio=0.0,
        mixed=False,
        cold_start=True,
        sqlite_enabled=True,
        decorator_map_width=4,
        cpu_bound=True,
        map_runner="process",
    )
    sample = _run_recipe_iteration(recipe, seed=42, warmup=False)
    assert sample.wall_clock_seconds > 0.0
    assert "decorator_map_micro.invocation_ms" in sample.latency_ms


def test_cpu_submit_threadpool_smoke() -> None:
    recipe = WorkloadRecipe(
        name="unit_cpu_submit_thread",
        flow_count=1,
        tasks_per_flow=0,
        task_events_per_task=0,
        read_ratio=0.0,
        mixed=False,
        cold_start=True,
        sqlite_enabled=True,
        decorator_submit_width=4,
        cpu_bound=True,
        map_runner="thread",
    )
    sample = _run_recipe_iteration(recipe, seed=42, warmup=False)
    assert sample.wall_clock_seconds > 0.0
    assert "decorator_submit_micro.invocation_ms" in sample.latency_ms
