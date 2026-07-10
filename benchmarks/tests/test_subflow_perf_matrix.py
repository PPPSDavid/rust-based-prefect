from __future__ import annotations

import os

# Apply before any InMemoryControlPlane is constructed in this module.
os.environ.setdefault("IRONFLOW_USE_RUST_FSM", "0")

import benchmarks.subflow_perf  # noqa: F401

from benchmarks.perf_matrix import (
    WorkloadRecipe,
    _recipe_catalog,
    _run_recipe_iteration,
    run_suite,
)

_SUBFLOW_PROFILES = (
    "inline_depth",
    "deploy_wait_chain",
    "deploy_cross_pool",
    "fire_forget_burst",
    "cancel_propagation",
    "query_dag_nested",
)


def test_subflow_iteration_smoke_inline_depth() -> None:
    recipe = WorkloadRecipe(
        name="unit_subflow_inline",
        flow_count=2,
        tasks_per_flow=2,
        task_events_per_task=2,
        read_ratio=0.0,
        mixed=False,
        cold_start=True,
        sqlite_enabled=True,
        subflow_profile="inline_depth",
    )
    sample = _run_recipe_iteration(recipe, seed=42, warmup=False)
    assert sample.wall_clock_seconds > 0.0
    assert "subflow.inline_depth_ms" in sample.latency_ms
    assert sample.counts["inline_depth_runs"] == 2


def test_subflow_iteration_smoke_deploy_wait_chain() -> None:
    recipe = WorkloadRecipe(
        name="unit_subflow_deploy",
        flow_count=2,
        tasks_per_flow=1,
        task_events_per_task=2,
        read_ratio=0.0,
        mixed=False,
        cold_start=True,
        sqlite_enabled=True,
        subflow_profile="deploy_wait_chain",
    )
    sample = _run_recipe_iteration(recipe, seed=42, warmup=False)
    assert sample.wall_clock_seconds > 0.0
    assert "subflow.deploy_wait_ms" in sample.latency_ms


def test_subflow_catalog_recipes_exist() -> None:
    catalog = _recipe_catalog()
    for name in (
        "subflow_inline_depth_3",
        "subflow_deploy_wait_chain",
        "subflow_deploy_cross_pool",
        "subflow_fire_forget_burst",
        "subflow_cancel_propagation",
        "subflow_query_dag_nested",
    ):
        assert name in catalog
        assert catalog[name].subflow_profile is not None


def test_subflow_lite_preset_run_suite_smoke() -> None:
    payload = run_suite(
        recipes=["subflow_inline_depth_3", "subflow_query_dag_nested"],
        repetitions=1,
        warmups=0,
        seed=7,
        jobs=1,
    )
    names = {row["recipe"] for row in payload["aggregates"]}
    assert names == {"subflow_inline_depth_3", "subflow_query_dag_nested"}


def test_all_subflow_profiles_run() -> None:
    for profile in _SUBFLOW_PROFILES:
        recipe = WorkloadRecipe(
            name=f"unit_{profile}",
            flow_count=5 if profile == "fire_forget_burst" else (3 if profile == "deploy_wait_chain" else 2),
            tasks_per_flow=2 if profile == "query_dag_nested" else 1,
            task_events_per_task=1,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            subflow_profile=profile,
        )
        sample = _run_recipe_iteration(recipe, seed=99, warmup=False)
        assert sample.wall_clock_seconds > 0.0, profile
        assert any(k.startswith("subflow.") for k in sample.latency_ms), profile
