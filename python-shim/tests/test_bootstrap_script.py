from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


def test_docs_reference_bootstrap_and_doctor():
    readme = Path("README.md").read_text(encoding="utf-8")
    install = Path("docs/INSTALL.md").read_text(encoding="utf-8")
    hosted = Path("docs/SELF_HOSTED_SERVER.md").read_text(encoding="utf-8")
    assert "python scripts/bootstrap.py" in readme
    assert "python scripts/bootstrap.py" in install
    assert "python scripts/ironflow_server.py doctor" in hosted
