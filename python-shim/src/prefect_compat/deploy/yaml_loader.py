from __future__ import annotations

from pathlib import Path

import yaml

from .spec import FlowOxideManifest


def load_manifest(path: str | Path) -> FlowOxideManifest:
    manifest_path = Path(path)
    with manifest_path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return FlowOxideManifest.model_validate(data or {})
