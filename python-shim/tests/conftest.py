"""Pytest fixtures: keep ``prefect_compat.decorators`` control plane aligned with the server."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from prefect_compat.control_plane_registry import set_control_plane
from prefect_compat.forecast_compile import clear_forecast_cache
from prefect_compat.process_workers import task_process_registry
from prefect_compat.server import control_plane


@pytest.fixture(autouse=True)
def _reset_global_control_plane_after_test() -> Generator[None, None, None]:
    clear_forecast_cache()
    yield
    task_process_registry().clear()
    clear_forecast_cache()
    set_control_plane(control_plane)
