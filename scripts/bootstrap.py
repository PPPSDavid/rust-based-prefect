#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_TEST = "python-shim/tests/test_compat.py::test_submit_chain_and_map"

_RUST_BUILD_FAILURE_SUFFIX = " See docs/INSTALL.md §4 (Build the Rust engine) — ensure rustup stable is installed."
_SMOKE_FAILURE_SUFFIX = (
    " See docs/INSTALL.md §5 (Check that it works) and verify FLOWOXIDE_RUST_LIB if a "
    "non-default native lib path is used."
)


def _resolve_python_hint() -> tuple[str | None, list[str]]:
    diagnostics: list[str] = []
    python_bin = shutil.which("python")
    py_launcher = shutil.which("py")

    if python_bin:
        diagnostics.append(f"[ok] python found: {python_bin}")
        return python_bin, diagnostics

    diagnostics.append("[missing] `python` was not found on PATH")
    if py_launcher:
        diagnostics.append(f"[hint] Windows Python launcher found: {py_launcher}")
        diagnostics.append("[hint] Try: py -3 -m pip install -r requirements-ci.txt")
    diagnostics.append(
        "[hint] Install Python 3.11+ and ensure `python` is on PATH (or use `py`)."
    )

    return None, diagnostics


def _run_checked(
    cmd: list[str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)


def _check_tooling(*, pytest_mode: str) -> tuple[bool, list[str]]:
    diagnostics: list[str] = []
    python_bin, python_hints = _resolve_python_hint()
    diagnostics.extend(python_hints)

    cargo_bin = shutil.which("cargo")

    if cargo_bin:
        diagnostics.append(f"[ok] cargo found: {cargo_bin}")
    else:
        diagnostics.append("[missing] cargo was not found on PATH")
        diagnostics.append("Install Rust via https://rustup.rs and reopen your shell.")

    pytest_ok = True
    if find_spec("pytest") is None:
        pytest_ok = False
        if pytest_mode == "require":
            diagnostics.append("[missing] pytest is not installed in this interpreter")
            diagnostics.append(
                "[hint] From repo root: python -m pip install -r requirements-ci.txt"
            )
        elif pytest_mode == "warn":
            diagnostics.append(
                "[warn] pytest is not installed (needed for smoke verification)"
            )
            diagnostics.append(
                "[hint] Install dev deps: python -m pip install -r requirements-ci.txt"
            )

    tooling_ok = python_bin is not None and cargo_bin is not None
    if pytest_mode == "require":
        tooling_ok = tooling_ok and pytest_ok

    return (tooling_ok, diagnostics)


def _build_rust() -> tuple[bool, str]:
    result = _run_checked(
        ["cargo", "build", "--manifest-path", "rust-engine/Cargo.toml"], cwd=REPO_ROOT
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "cargo build failed"
        return False, f"Rust build failed: {details}{_RUST_BUILD_FAILURE_SUFFIX}"
    return True, "Rust build succeeded."


def _smoke_verify() -> tuple[bool, str]:
    result = _run_checked(
        [sys.executable, "-m", "pytest", SMOKE_TEST, "-q"], cwd=REPO_ROOT
    )
    if result.returncode != 0:
        details = (
            result.stderr.strip()
            or result.stdout.strip()
            or "smoke verification failed"
        )
        return False, f"Smoke verification failed: {details}{_SMOKE_FAILURE_SUFFIX}"
    return True, "Smoke verification passed."


def _rust_lib_env_set() -> bool:
    return bool(os.environ.get("FLOWOXIDE_RUST_LIB", "").strip())


def _native_check() -> int:
    """PyPI / wheel smoke: no pytest, cargo, or repo layout required."""
    try:
        from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task
        from prefect_compat.rust_bridge import native_library_available
    except ImportError as exc:
        print(f"[error] Cannot import prefect_compat: {exc}")
        print("[hint] Install: python -m pip install flowoxide-prefect-compat")
        print("[hint] See docs/INSTALL.md §1 (PyPI — pip / uv).")
        return 1

    native_ok = native_library_available()
    print(f"native_library_available: {native_ok}")

    if not native_ok and not _rust_lib_env_set():
        print("[error] Native library is not available and FLOWOXIDE_RUST_LIB is unset.")
        print("[hint] Supported wheels bundle the native library (docs/INSTALL.md §1).")
        print(
            "[hint] Source builds: docs/INSTALL.md §4; FLOWOXIDE_RUST_LIB: docs/how-to/setup.md."
        )
        return 1

    set_control_plane(InMemoryControlPlane())

    @task
    def _bootstrap_native_task() -> int:
        return 42

    @flow
    def _bootstrap_native_flow() -> int:
        return cast(Any, _bootstrap_native_task).submit().result()

    try:
        flow_out = _bootstrap_native_flow()
    except Exception as exc:
        print(f"[error] Minimal in-process flow failed: {exc}")
        return 1

    print(f"minimal_flow_result: {flow_out}")
    print(
        "[ok] Native check passed (in-process smoke only; not a Prefect feature parity check)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rust-first bootstrap: validate tooling, build, then run smoke verification; "
            "or run --native-check for a PyPI/wheel-friendly import and flow smoke."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check-only",
        action="store_true",
        help="Only run toolchain diagnostics and print remediation hints (repo dev).",
    )
    mode.add_argument(
        "--smoke-only",
        action="store_true",
        help="Skip cargo build and run only smoke verification (repo dev).",
    )
    mode.add_argument(
        "--native-check",
        action="store_true",
        help=(
            "PyPI/wheel path: import prefect_compat, print native_library_available(), "
            "run a minimal @flow + @task (no pytest/cargo/repo layout)."
        ),
    )
    args = parser.parse_args(argv)

    if args.native_check:
        return _native_check()

    if args.check_only:
        pytest_mode = "warn"
    elif args.smoke_only:
        pytest_mode = "require"
    else:
        pytest_mode = "require"

    ok, diagnostics = _check_tooling(pytest_mode=pytest_mode)
    for line in diagnostics:
        print(line)
    if not ok:
        return 1
    if args.check_only:
        print("Toolchain diagnostics passed.")
        return 0

    if not args.smoke_only:
        build_ok, build_message = _build_rust()
        print(build_message)
        if not build_ok:
            return 1

    smoke_ok, smoke_message = _smoke_verify()
    print(smoke_message)
    if not smoke_ok:
        return 1

    print("Bootstrap checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
