"""Smoke tests for perf_matrix gcl preset recipes."""

from __future__ import annotations

from benchmarks.perf_matrix import _presets, _recipe_catalog, _run_gcl_iteration


def test_gcl_preset_recipes_exist() -> None:
    catalog = _recipe_catalog()
    for name in _presets()["gcl"]:
        assert name in catalog
        assert catalog[name].gcl_profile is not None


def test_gcl_acquire_micro_runs() -> None:
    recipe = _recipe_catalog()["gcl_acquire_micro"]
    sample = _run_gcl_iteration(recipe, seed=1, warmup=True)
    assert sample.wall_clock_seconds >= 0.0
    assert "gcl.acquire_release_ms" in sample.latency_ms
