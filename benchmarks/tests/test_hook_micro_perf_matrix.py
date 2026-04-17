from __future__ import annotations

from benchmarks.perf_matrix import (
    WorkloadRecipe,
    _run_recipe_iteration,
    run_suite,
)


def test_decorator_hook_micro_iteration_smoke() -> None:
    recipe = WorkloadRecipe(
        name="unit_micro_hooks",
        flow_count=4,
        tasks_per_flow=2,
        task_events_per_task=0,
        read_ratio=0.0,
        mixed=False,
        cold_start=True,
        sqlite_enabled=True,
        decorator_hook_profile="both",
    )
    sample = _run_recipe_iteration(recipe, seed=42, warmup=False)
    assert sample.wall_clock_seconds > 0.0
    assert "decorator_hook_micro.invocation_ms" in sample.latency_ms
    assert sample.throughput["flows_per_sec"] > 0.0


def test_hook_micro_preset_run_suite_smoke() -> None:
    payload = run_suite(
        recipes=["micro_decorator_hooks_none", "micro_decorator_hooks_task_noop"],
        repetitions=1,
        warmups=0,
        seed=7,
        jobs=1,
    )
    names = {row["recipe"] for row in payload["aggregates"]}
    assert names == {"micro_decorator_hooks_none", "micro_decorator_hooks_task_noop"}
