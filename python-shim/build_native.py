"""Setuptools hook: build rust-engine cdylib and copy into prefect_compat/native for wheels."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import warnings
from pathlib import Path

from setuptools.command.build_py import build_py as _build_py

_PYTHON_SHIM_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _PYTHON_SHIM_ROOT.parent
_RUST_MANIFEST = _REPO_ROOT / "rust-engine" / "Cargo.toml"
_NATIVE_DEST = _PYTHON_SHIM_ROOT / "src" / "prefect_compat" / "native"


def _artifact_filename() -> str | None:
    system = platform.system()
    if system == "Linux":
        return "libironflow_engine.so"
    if system == "Darwin":
        return "libironflow_engine.dylib"
    if system == "Windows":
        return "ironflow_engine.dll"
    return None


def _release_artifact_path() -> Path | None:
    name = _artifact_filename()
    if name is None:
        return None
    return _REPO_ROOT / "rust-engine" / "target" / "release" / name


def maybe_build_and_stage_native_library() -> None:
    """When ``cargo`` is available and the repo layout is present, build release cdylib into native/."""
    if os.environ.get("IRONFLOW_SKIP_NATIVE_BUILD", "").lower() in ("1", "true", "yes"):
        return
    if not _RUST_MANIFEST.is_file():
        warnings.warn(
            "ironflow: rust-engine manifest not found; skipping native library staging "
            f"(expected {_RUST_MANIFEST})",
            stacklevel=2,
        )
        return
    if shutil.which("cargo") is None:
        warnings.warn(
            "ironflow: cargo not on PATH; wheel/sdist build will not bundle ironflow_engine "
            "(set IRONFLOW_SKIP_NATIVE_BUILD=1 to silence)",
            stacklevel=2,
        )
        return

    name = _artifact_filename()
    if name is None:
        warnings.warn(
            f"ironflow: unsupported platform for native staging: {platform.system()}",
            stacklevel=2,
        )
        return

    subprocess.run(
        ["cargo", "build", "--release", f"--manifest-path={_RUST_MANIFEST}"],
        cwd=str(_REPO_ROOT),
        check=True,
    )
    built = _release_artifact_path()
    if built is None or not built.is_file():
        raise RuntimeError(f"ironflow: cargo build finished but artifact missing: {built}")

    _NATIVE_DEST.mkdir(parents=True, exist_ok=True)
    dest = _NATIVE_DEST / name
    shutil.copy2(built, dest)


class build_py(_build_py):
    def run(self) -> None:
        maybe_build_and_stage_native_library()
        super().run()
