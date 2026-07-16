"""Catalog/preset coverage for resolve_terminal_micro."""

from benchmarks.perf_matrix import _presets, _recipe_catalog


def test_resolve_terminal_recipe_and_preset() -> None:
    catalog = _recipe_catalog()
    assert "resolve_terminal_micro" in catalog
    recipe = catalog["resolve_terminal_micro"]
    assert recipe.resolve_terminal_children == 64
    assert "resolve_terminal_micro" in _presets()["final_state"]
