#!/usr/bin/env python
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_TEST = "python-shim/tests/test_compat.py::test_submit_chain_and_map"


def _run_checked(
    cmd: list[str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)


def _check_tooling() -> tuple[bool, list[str]]:
    diagnostics: list[str] = []
    python_bin = shutil.which("python")
    cargo_bin = shutil.which("cargo")

    if python_bin:
        diagnostics.append(f"[ok] python found: {python_bin}")
    else:
        diagnostics.append("[missing] python was not found on PATH")
        diagnostics.append("Install Python 3.11+ and ensure `python` is available on PATH.")

    if cargo_bin:
        diagnostics.append(f"[ok] cargo found: {cargo_bin}")
    else:
        diagnostics.append("[missing] cargo was not found on PATH")
        diagnostics.append("Install Rust via https://rustup.rs and reopen your shell.")

    return (python_bin is not None and cargo_bin is not None, diagnostics)


def _build_rust() -> tuple[bool, str]:
    result = _run_checked(
        ["cargo", "build", "--manifest-path", "rust-engine/Cargo.toml"], cwd=REPO_ROOT
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "cargo build failed"
        return False, f"Rust build failed: {details}"
    return True, "Rust build succeeded."


def _smoke_verify() -> tuple[bool, str]:
    result = _run_checked([sys.executable, "-m", "pytest", SMOKE_TEST, "-q"], cwd=REPO_ROOT)
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "smoke verification failed"
        return False, f"Smoke verification failed: {details}"
    return True, "Smoke verification passed."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rust-first bootstrap: validate tooling, build, then run smoke verification."
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only run toolchain diagnostics and print remediation hints.",
    )
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Skip cargo build and run only smoke verification.",
    )
    args = parser.parse_args(argv)

    ok, diagnostics = _check_tooling()
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
