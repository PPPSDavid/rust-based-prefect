from __future__ import annotations

import importlib

from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.bootstrap as bootstrap


def test_bootstrap_reports_missing_cargo(monkeypatch, capsys):
    monkeypatch.setattr(
        bootstrap.shutil,
        "which",
        lambda name: None if name == "cargo" else "C:/Python/python.exe",
    )

    rc = bootstrap.main(["--check-only"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "cargo was not found on PATH" in out
    assert "https://rustup.rs" in out


def test_bootstrap_hints_windows_py_launcher_when_python_missing(monkeypatch, capsys):
    def fake_which(name: str):
        if name == "py":
            return "C:/Python/py.exe"
        if name == "cargo":
            return None
        return None

    monkeypatch.setattr(bootstrap.shutil, "which", fake_which)

    rc = bootstrap.main(["--check-only"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "Python launcher found" in out


def test_bootstrap_check_only_warns_when_pytest_missing(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "present")
    real_find_spec = bootstrap.find_spec

    def fake_find_spec(name):
        if name == "pytest":
            return None
        return real_find_spec(name)

    monkeypatch.setattr(bootstrap, "find_spec", fake_find_spec)

    rc = bootstrap.main(["--check-only"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "pytest is not installed" in out


def test_bootstrap_smoke_only_fails_when_pytest_missing(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "present")
    real_find_spec = bootstrap.find_spec

    def fake_find_spec(name):
        if name == "pytest":
            return None
        return real_find_spec(name)

    monkeypatch.setattr(bootstrap, "find_spec", fake_find_spec)

    rc = bootstrap.main(["--smoke-only"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "pytest is not installed" in out


def test_bootstrap_build_failure_prints_install_hint(monkeypatch, capsys):
    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="cargo error detail")

    monkeypatch.setattr(bootstrap, "_run_checked", fake_run)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "present")

    rc = bootstrap.main([])
    out = capsys.readouterr().out

    assert rc == 1
    assert "docs/INSTALL.md §4" in out


def test_bootstrap_runs_build_and_smoke(monkeypatch, capsys):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bootstrap, "_run_checked", fake_run)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "present")

    rc = bootstrap.main([])
    out = capsys.readouterr().out

    assert rc == 0
    assert calls[0][:3] == ["cargo", "build", "--manifest-path"]
    assert calls[1][0:3] == [bootstrap.sys.executable, "-m", "pytest"]
    assert "Bootstrap checks passed." in out


def test_bootstrap_fails_when_smoke_fails(monkeypatch, capsys):
    def fake_run(cmd, **kwargs):
        if cmd[0] == "cargo":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="smoke boom", stderr="")

    monkeypatch.setattr(bootstrap, "_run_checked", fake_run)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "present")

    rc = bootstrap.main([])
    out = capsys.readouterr().out

    assert rc == 1
    assert "Smoke verification failed" in out
    assert "docs/INSTALL.md §5" in out


def test_bootstrap_native_check_runs_minimal_flow(monkeypatch, capsys):
    """In-process smoke only; native availability is patched so cargo is not required."""
    rb = importlib.import_module("prefect_compat.rust_bridge")
    monkeypatch.setattr(rb, "native_library_available", lambda: True)

    rc = bootstrap.main(["--native-check"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "native_library_available: True" in out
    assert "minimal_flow_result: 42" in out
    assert "[ok] Native check passed" in out


def test_bootstrap_native_check_fails_without_native_or_flowoxide_lib(
    monkeypatch, capsys
):
    rb = importlib.import_module("prefect_compat.rust_bridge")
    monkeypatch.setattr(rb, "native_library_available", lambda: False)
    monkeypatch.delenv("FLOWOXIDE_RUST_LIB", raising=False)

    rc = bootstrap.main(["--native-check"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "native_library_available: False" in out
    assert "FLOWOXIDE_RUST_LIB" in out


def test_bootstrap_native_check_mutually_exclusive_with_check_only():
    with pytest.raises(SystemExit):
        bootstrap.main(["--native-check", "--check-only"])


def test_bootstrap_native_check_passes_when_flowoxide_lib_set_despite_native_false(
    monkeypatch, capsys
):
    rb = importlib.import_module("prefect_compat.rust_bridge")
    monkeypatch.setattr(rb, "native_library_available", lambda: False)
    monkeypatch.setenv("FLOWOXIDE_RUST_LIB", "/tmp/flowoxide_engine_dummy.so")

    rc = bootstrap.main(["--native-check"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "native_library_available: False" in out
    assert "[ok] Native check passed" in out


def test_docs_reference_bootstrap_and_doctor():
    readme = Path("README.md").read_text(encoding="utf-8")
    install = Path("docs/INSTALL.md").read_text(encoding="utf-8")
    hosted = Path("docs/SELF_HOSTED_SERVER.md").read_text(encoding="utf-8")
    assert "python scripts/bootstrap.py" in readme
    assert "python scripts/bootstrap.py" in install
    assert "python scripts/bootstrap.py --native-check" in install
    assert "python scripts/flowoxide_server.py doctor" in hosted
