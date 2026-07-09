from __future__ import annotations

from pathlib import Path

import yaml

from .spec import IronflowManifest


def load_manifest(path: str | Path) -> IronflowManifest:
    manifest_path = Path(path)
    with manifest_path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return IronflowManifest.model_validate(data or {})
