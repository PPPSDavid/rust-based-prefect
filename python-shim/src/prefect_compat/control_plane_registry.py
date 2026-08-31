"""Process-local default control plane for in-process flow/task execution."""

from __future__ import annotations

import multiprocessing as mp

from .runtime import InMemoryControlPlane

_CONTROL_PLANE: InMemoryControlPlane | None = None


def _require_control_plane() -> InMemoryControlPlane:
    """Return the active control plane, creating the default in MainProcess when unset."""
    global _CONTROL_PLANE
    if _CONTROL_PLANE is None:
        _CONTROL_PLANE = InMemoryControlPlane()
    return _CONTROL_PLANE


def set_control_plane(control_plane: InMemoryControlPlane) -> None:
    global _CONTROL_PLANE
    _CONTROL_PLANE = control_plane


# Spawned process-pool workers import ``prefect_compat`` to unpickle task bodies.
# Skip eager SQLite open in children so parallel map workers do not race the default DB.
if mp.current_process().name == "MainProcess":
    _CONTROL_PLANE = InMemoryControlPlane()
