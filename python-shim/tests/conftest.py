"""Pytest fixtures: keep ``prefect_compat.decorators`` control plane aligned with the server."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from prefect_compat.decorators import set_control_plane
from prefect_compat.process_workers import task_process_registry
from prefect_compat.server import control_plane


@pytest.fixture(autouse=True)
def _reset_global_control_plane_after_test() -> Generator[None, None, None]:
    yield
    task_process_registry().clear()
    set_control_plane(control_plane)
