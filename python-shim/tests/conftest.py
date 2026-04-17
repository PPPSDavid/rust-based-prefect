"""Pytest fixtures: keep ``prefect_compat.decorators`` control plane aligned with the server."""

from __future__ import annotations

import pytest

from prefect_compat.decorators import set_control_plane
from prefect_compat.server import control_plane


@pytest.fixture(autouse=True)
def _reset_global_control_plane_after_test() -> None:
    yield
    set_control_plane(control_plane)
