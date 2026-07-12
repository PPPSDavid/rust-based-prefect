"""Unit tests for ironflow_engine discovery (env, checkout, packaged resources)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _reload_rust_bridge(
    monkeypatch: pytest.MonkeyPatch, **env: str | None
) -> ModuleType:
    monkeypatch.delenv("IRONFLOW_RUST_LIB", raising=False)
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    name = "prefect_compat.rust_bridge"
    if name in sys.modules:
        del sys.modules[name]
    return importlib.import_module(name)


def test_find_repo_root_when_under_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_file = REPO_ROOT / "python-shim" / "src" / "prefect_compat" / "rust_bridge.py"
    rb = _reload_rust_bridge(monkeypatch)
    monkeypatch.setattr(rb, "__file__", str(fake_file))
    assert rb._find_repo_root_with_rust() == REPO_ROOT


def test_find_repo_root_none_when_not_in_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    rb = _reload_rust_bridge(monkeypatch)
    monkeypatch.setattr(
        rb, "__file__", "/tmp/fake-site-packages/prefect_compat/rust_bridge.py"
    )
    assert rb._find_repo_root_with_rust() is None


def test_ironflow_rust_lib_missing_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "missing.so"
    rb = _reload_rust_bridge(monkeypatch, IRONFLOW_RUST_LIB=str(missing))
    with pytest.raises(RuntimeError, match="IRONFLOW_RUST_LIB"):
        rb.load_ironflow_library()


def test_packaged_resource_exists_true(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib import resources

    rb = _reload_rust_bridge(monkeypatch)
    libname = rb._platform_lib_names()[0]

    class Native:
        def __truediv__(self, name: str) -> object:
            assert name == libname
            leaf = MagicMock()
            leaf.is_file.return_value = True
            return leaf

    class Root:
        def __truediv__(self, name: str) -> Native | MagicMock:
            if name == "native":
                return Native()
            return MagicMock()

    def fake_files(anchor: object) -> Root:
        assert anchor == "prefect_compat"
        return Root()

    monkeypatch.setattr(resources, "files", fake_files)
    assert rb._packaged_resource_exists() is True


def test_packaged_resource_exists_false_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib import resources

    rb = _reload_rust_bridge(monkeypatch)
    libname = rb._platform_lib_names()[0]

    class EmptyRoot:
        def __truediv__(self, name: str) -> object:
            if name != "native":
                return MagicMock()
            native = MagicMock()

            def child_div(other: str) -> MagicMock:
                assert other == libname
                leaf = MagicMock()
                leaf.is_file.return_value = False
                return leaf

            native.__truediv__ = child_div
            return native

    monkeypatch.setattr(
        resources,
        "files",
        lambda a: EmptyRoot() if a == "prefect_compat" else MagicMock(),
    )
    assert rb._packaged_resource_exists() is False
