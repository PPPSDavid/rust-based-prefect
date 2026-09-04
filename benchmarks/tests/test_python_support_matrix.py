"""Tests for scripts/check_python_support_matrix.py."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_python_support_matrix.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_python_support_matrix", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prerelease_and_eol_filtered() -> None:
    mod = _load_module()
    cycles = [
        {"cycle": "3.15", "eol": "2031-10-31", "latest": "3.15.0rc2"},
        {"cycle": "3.14", "eol": "2030-10-31", "latest": "3.14.7"},
        {"cycle": "3.11", "eol": "2027-10-31", "latest": "3.11.16"},
        {"cycle": "3.10", "eol": "2026-10-31", "latest": "3.10.21"},
        {"cycle": "3.9", "eol": "2025-10-31", "latest": "3.9.25"},
    ]
    got = mod.expected_minors_from_cycles(cycles, ">=3.11", today=date(2026, 9, 4))
    assert got == ["3.11", "3.14"]


def test_cibw_ignores_freethreaded_tag() -> None:
    mod = _load_module()
    text = 'CIBW_BUILD: "cp311-* cp312-* cp314t-*"\n'
    tags = mod.parse_cibw_gil_minors(text)
    assert tags == [{"3.11", "3.12"}]


def test_repo_check_offline_passes() -> None:
    mod = _load_module()
    rc = mod.main(["--offline", "--root", str(ROOT)])
    assert rc == 0


def test_missing_classifier_fails(tmp_path: Path) -> None:
    mod = _load_module()
    shim = tmp_path / "python-shim"
    shim.mkdir()
    (shim / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.11"\n'
        'classifiers = ["Programming Language :: Python :: 3.11"]\n',
        encoding="utf-8",
    )
    (tmp_path / "static-planner").mkdir()
    (tmp_path / "static-planner" / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        'python-version: ["3.11", "3.12"]\nCIBW_BUILD: "cp311-* cp312-*"\n',
        encoding="utf-8",
    )
    (wf / "publish-pypi.yml").write_text(
        'python-version: "3.11"\npython-version: "3.12"\nCIBW_BUILD: "cp311-* cp312-*"\n',
        encoding="utf-8",
    )
    (wf / "publish-testpypi.yml").write_text(
        'python-version: "3.11"\npython-version: "3.12"\nCIBW_BUILD: "cp311-* cp312-*"\n',
        encoding="utf-8",
    )
    errors = mod.check_repo(tmp_path, expected=["3.11", "3.12"])
    assert any("classifiers missing" in e for e in errors)


def test_require_freethreaded_flag() -> None:
    mod = _load_module()
    assert mod.has_freethreaded_locus('python-version: "3.14t"\n', "3.14")
    assert not mod.has_freethreaded_locus('python-version: "3.14"\n', "3.14")


def test_require_freethreaded_on_repo() -> None:
    mod = _load_module()
    rc = mod.main(["--offline", "--root", str(ROOT), "--require-freethreaded", "3.14"])
    assert rc == 0
