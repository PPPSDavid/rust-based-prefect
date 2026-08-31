from __future__ import annotations

from benchmarks.perf_matrix import (
    WorkloadRecipe,
    _presets,
    _recipe_catalog,
    _run_recipe_iteration,
    canonical_matrix_compare_key,
    run_suite,
)


def test_flow_submit_preset_recipes_exist() -> None:
    catalog = _recipe_catalog()
    for name in _presets()["flow_submit"]:
        assert name in catalog
        recipe = catalog[name]
        assert recipe.decorator_submit_width is not None
        assert recipe.decorator_submit_width > 0


def test_canonical_key_matches_flow_submit_preset() -> None:
    recipes = _presets()["flow_submit"]
    assert canonical_matrix_compare_key(recipes) == "preset:flow_submit"


def test_decorator_submit_micro_iteration_smoke() -> None:
    recipe = WorkloadRecipe(
        name="unit_micro_submit",
        flow_count=2,
        tasks_per_flow=1,
        task_events_per_task=0,
        read_ratio=0.0,
        mixed=False,
        cold_start=True,
        sqlite_enabled=True,
        decorator_submit_width=8,
    )
    sample = _run_recipe_iteration(recipe, seed=42, warmup=False)
    assert sample.wall_clock_seconds > 0.0
    assert "decorator_submit_micro.invocation_ms" in sample.latency_ms
    assert sample.counts["tasks_created"] == 2 * 8
    # Prefer Rust FSM when the native engine loads in this environment.
    assert sample.counts.get("rust_fsm_active") in (0, 1)


def test_flow_submit_preset_run_suite_smoke() -> None:
    payload = run_suite(
        recipes=["micro_submit_threadpool_narrow"],
        repetitions=1,
        warmups=0,
        seed=7,
        jobs=1,
    )
    names = {row["recipe"] for row in payload["aggregates"]}
    assert names == {"micro_submit_threadpool_narrow"}
