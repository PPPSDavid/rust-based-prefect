"""Process-wide control plane for the HTTP app and embedded local worker."""

from __future__ import annotations

import os
from pathlib import Path

from .control_plane_registry import set_control_plane
from .runtime import InMemoryControlPlane

history_path = os.getenv("IRONFLOW_HISTORY_PATH")
if history_path is None:
    history_path = str(Path("data") / "ironflow_history.jsonl")

control_plane = InMemoryControlPlane(history_path=history_path)
set_control_plane(control_plane)
