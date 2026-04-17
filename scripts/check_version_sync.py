#!/usr/bin/env python3
"""Verify repo VERSION matches rust-engine and Python package versions."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"


def _cargo_package_version(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    in_package = False
    for line in text.splitlines():
        s = line.strip()
        if s == "[package]":
            in_package = True
            continue
        if s.startswith("[") and in_package:
            break
        if in_package:
            m = re.match(r'version\s*=\s*"([^"]+)"', s)
            if m:
                return m.group(1)
    return None


def _pyproject_project_version(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    in_project = False
    for line in text.splitlines():
        s = line.strip()
        if s == "[project]":
            in_project = True
            continue
        if s.startswith("[") and in_project:
            break
        if in_project:
            m = re.match(r'version\s*=\s*"([^"]+)"', s)
            if m:
                return m.group(1)
    return None


def main() -> int:
    if not VERSION_FILE.is_file():
        print("VERSION file missing at repo root", file=sys.stderr)
        return 1
    expected = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not expected:
        print("VERSION is empty", file=sys.stderr)
        return 1

    frontend_pkg = ROOT / "frontend" / "package.json"
    frontend_ver: str | None = None
    if frontend_pkg.is_file():
        data = json.loads(frontend_pkg.read_text(encoding="utf-8"))
        v = data.get("version")
        frontend_ver = str(v) if isinstance(v, str) else None

    checks: list[tuple[str, Path, str | None]] = [
        ("rust-engine [package].version", ROOT / "rust-engine" / "Cargo.toml", _cargo_package_version(ROOT / "rust-engine" / "Cargo.toml")),
        ("python-shim [project].version", ROOT / "python-shim" / "pyproject.toml", _pyproject_project_version(ROOT / "python-shim" / "pyproject.toml")),
        ("static-planner [project].version", ROOT / "static-planner" / "pyproject.toml", _pyproject_project_version(ROOT / "static-planner" / "pyproject.toml")),
        ("frontend package.json version", frontend_pkg, frontend_ver),
    ]

    failed = False
    for label, path, found in checks:
        if found is None:
            print(f"{label}: could not find version in {path}", file=sys.stderr)
            failed = True
            continue
        if found != expected:
            print(f"{label}: expected {expected!r}, found {found!r}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
