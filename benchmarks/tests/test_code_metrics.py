"""Tests for scripts/code_metrics.py (file-LOC ratchet)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "code_metrics.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("code_metrics", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_new_file_over_cap_fails() -> None:
    mod = _load_module()
    errors = mod.check(
        {"python-shim/src/prefect_compat/brand_new.py": 801},
        {"files": {}, "allowlist": {}},
        new_file_cap=800,
        hard_cap=1000,
    )
    assert errors
    assert "brand_new.py" in errors[0]


def test_allowlisted_file_must_not_grow() -> None:
    mod = _load_module()
    errors = mod.check(
        {"python-shim/src/prefect_compat/runtime.py": 5300},
        {
            "files": {"python-shim/src/prefect_compat/runtime.py": 5224},
            "allowlist": {"python-shim/src/prefect_compat/runtime.py": 5224},
        },
        new_file_cap=800,
        hard_cap=1000,
    )
    assert errors
    assert "grew" in errors[0]


def test_allowlisted_file_may_shrink() -> None:
    mod = _load_module()
    errors = mod.check(
        {"python-shim/src/prefect_compat/runtime.py": 400},
        {
            "files": {"python-shim/src/prefect_compat/runtime.py": 5224},
            "allowlist": {"python-shim/src/prefect_compat/runtime.py": 5224},
        },
        new_file_cap=800,
        hard_cap=1000,
    )
    assert errors == []


def test_existing_file_under_hard_cap_ok() -> None:
    mod = _load_module()
    errors = mod.check(
        {"python-shim/src/prefect_compat/server.py": 856},
        {"files": {"python-shim/src/prefect_compat/server.py": 856}, "allowlist": {}},
        new_file_cap=800,
        hard_cap=1000,
    )
    assert errors == []


def test_existing_file_crossing_hard_cap_fails() -> None:
    mod = _load_module()
    errors = mod.check(
        {"python-shim/src/prefect_compat/server.py": 1001},
        {"files": {"python-shim/src/prefect_compat/server.py": 856}, "allowlist": {}},
        new_file_cap=800,
        hard_cap=1000,
    )
    assert errors
    assert "hard cap" in errors[0]
