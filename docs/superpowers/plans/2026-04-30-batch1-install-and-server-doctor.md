# Batch 1: Install Bootstrap + Server Doctor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Rust-first onboarding path with a deterministic bootstrap command and actionable `ironflow_server.py --doctor` diagnostics.

**Architecture:** Keep orchestration behavior unchanged; add a new bootstrap script for environment verification and smoke validation, then harden server startup and diagnostics in `scripts/ironflow_server.py`. Use focused test files in `python-shim/tests` and keep docs updates limited to install/start paths.

**Tech Stack:** Python 3.11+, pytest, existing `scripts/` command-line patterns, Rust `cargo build`, FastAPI/Vite local stack.

---

## File Structure and Ownership

- Create: `scripts/bootstrap.py`
  - Purpose: validate local prerequisites, build Rust library, run smoke verification.
- Modify: `scripts/ironflow_server.py`
  - Purpose: add `--doctor` diagnostics and clearer preflight/start output.
- Create: `python-shim/tests/test_bootstrap_script.py`
  - Purpose: unit tests for bootstrap command behavior and error reporting.
- Modify: `python-shim/tests/test_ui_api.py`
  - Purpose: add diagnostics coverage around stack health expectations.
- Modify: `README.md`
  - Purpose: canonical install/start path with Rust-first checks.
- Modify: `docs/INSTALL.md`
  - Purpose: detailed bootstrap usage and troubleshooting.
- Modify: `docs/SELF_HOSTED_SERVER.md`
  - Purpose: document `start --doctor` output and remediation guidance.

---

### Task 1: Add Rust-First Bootstrap CLI

**Files:**

- Create: `scripts/bootstrap.py`
- Test: `python-shim/tests/test_bootstrap_script.py`
- **Step 1: Write the failing bootstrap command tests**

```python
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.bootstrap as bootstrap


def test_bootstrap_reports_missing_cargo(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: None if name == "cargo" else "python")
    rc = bootstrap.main(["--check-only"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "cargo was not found on PATH" in out


def test_bootstrap_smoke_success(monkeypatch, capsys, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bootstrap, "_run_checked", fake_run)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "present")
    monkeypatch.setenv("IRONFLOW_HISTORY_PATH", str(tmp_path / "history.jsonl"))
    rc = bootstrap.main(["--smoke-only"])
    out = capsys.readouterr().out
    assert rc == 0
    assert any("cargo" in cmd[0] for cmd in calls)
    assert "Bootstrap checks passed" in out
```

- **Step 2: Run tests to verify failure before implementation**

Run: `python -m pytest python-shim/tests/test_bootstrap_script.py -v`  
Expected: FAIL with import/module or missing function assertions.

- **Step 3: Implement bootstrap CLI with explicit checks**

```python
#!/usr/bin/env python
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_checked(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)


def _check_tooling() -> tuple[bool, list[str]]:
    problems = []
    if shutil.which("python") is None:
        problems.append("python was not found on PATH")
    if shutil.which("cargo") is None:
        problems.append("cargo was not found on PATH")
    return (len(problems) == 0, problems)


def _build_rust() -> tuple[bool, str]:
    result = _run_checked(["cargo", "build", "--manifest-path", "rust-engine/Cargo.toml"], cwd=REPO_ROOT)
    if result.returncode != 0:
        return False, result.stderr.strip() or "cargo build failed"
    return True, "rust-engine build succeeded"


def _smoke_verify() -> tuple[bool, str]:
    result = _run_checked([sys.executable, "-m", "pytest", "python-shim/tests/test_compat.py::test_submit_chain_and_map", "-q"], cwd=REPO_ROOT)
    if result.returncode != 0:
        return False, "smoke flow test failed"
    return True, "smoke flow test passed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap IronFlow environment with Rust-first checks.")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--smoke-only", action="store_true")
    args = parser.parse_args(argv)

    ok, problems = _check_tooling()
    if not ok:
        for problem in problems:
            print(problem)
        return 1

    if not args.check_only:
        build_ok, build_msg = _build_rust()
        print(build_msg)
        if not build_ok:
            return 1

    if args.check_only:
        print("Toolchain checks passed")
        return 0

    smoke_ok, smoke_msg = _smoke_verify()
    print(smoke_msg)
    if not smoke_ok:
        return 1

    print("Bootstrap checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- **Step 4: Run tests to verify pass**

Run: `python -m pytest python-shim/tests/test_bootstrap_script.py -v`  
Expected: PASS

- **Step 5: Commit Task 1 changes**

```bash
git add scripts/bootstrap.py python-shim/tests/test_bootstrap_script.py
git commit -m "feat(scripts): add rust-first bootstrap command with smoke checks"
```

---

### Task 2: Add `ironflow_server.py --doctor` and Preflight Diagnostics

**Files:**

- Modify: `scripts/ironflow_server.py`
- Test: `python-shim/tests/test_ui_api.py`
- **Step 1: Write failing diagnostics tests**

```python
from scripts import ironflow_server


def test_doctor_reports_backend_frontend_keys(capsys):
    rc = ironflow_server.main(["doctor"])
    out = capsys.readouterr().out
    assert rc in (0, 1)
    assert "backend_status" in out
    assert "frontend_status" in out
    assert "rust_library" in out
```

- **Step 2: Run test to verify failure**

Run: `python -m pytest python-shim/tests/test_ui_api.py::test_doctor_reports_backend_frontend_keys -v`  
Expected: FAIL with unknown command or missing output fields.

- **Step 3: Implement doctor command and structured output**

```python
def _doctor_snapshot() -> dict[str, str]:
    return {
        "backend_status": "ok" if _backend_imports_ready() else "missing-deps",
        "frontend_status": "ok" if _npm_ready() else "npm-missing",
        "rust_library": "found" if _rust_lib_ready() else "not-found",
    }


def doctor() -> int:
    snapshot = _doctor_snapshot()
    for key, value in snapshot.items():
        print(f"{key}: {value}")
    return 0 if all(v in ("ok", "found") for v in snapshot.values()) else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor()
    ...
```

- **Step 4: Re-run targeted test**

Run: `python -m pytest python-shim/tests/test_ui_api.py::test_doctor_reports_backend_frontend_keys -v`  
Expected: PASS

- **Step 5: Commit Task 2 changes**

```bash
git add scripts/ironflow_server.py python-shim/tests/test_ui_api.py
git commit -m "feat(scripts): add doctor diagnostics for local stack startup"
```

---

### Task 3: Document Canonical Install/Start Paths

**Files:**

- Modify: `README.md`
- Modify: `docs/INSTALL.md`
- Modify: `docs/SELF_HOSTED_SERVER.md`
- **Step 1: Add failing doc consistency test**

```python
from pathlib import Path


def test_docs_reference_bootstrap_and_doctor():
    readme = Path("README.md").read_text(encoding="utf-8")
    install = Path("docs/INSTALL.md").read_text(encoding="utf-8")
    hosted = Path("docs/SELF_HOSTED_SERVER.md").read_text(encoding="utf-8")
    assert "python scripts/bootstrap.py" in readme
    assert "python scripts/bootstrap.py" in install
    assert "python scripts/ironflow_server.py doctor" in hosted
```

- **Step 2: Run test to verify failure**

Run: `python -m pytest python-shim/tests/test_bootstrap_script.py::test_docs_reference_bootstrap_and_doctor -v`  
Expected: FAIL until docs are updated.

- **Step 3: Update docs with canonical flow**

```markdown
# README.md snippet
1. Run `python scripts/bootstrap.py`
2. Start local stack with `python scripts/ironflow_server.py start`
3. Run diagnostics anytime with `python scripts/ironflow_server.py doctor`
```

```markdown
# docs/INSTALL.md snippet
## Canonical setup
- `python scripts/bootstrap.py --check-only`
- `python scripts/bootstrap.py`
```

```markdown
# docs/SELF_HOSTED_SERVER.md snippet
## Doctor mode
Use `python scripts/ironflow_server.py doctor` to print backend/frontend/rust readiness and remediation hints.
```

- **Step 4: Re-run doc test**

Run: `python -m pytest python-shim/tests/test_bootstrap_script.py::test_docs_reference_bootstrap_and_doctor -v`  
Expected: PASS

- **Step 5: Commit Task 3 changes**

```bash
git add README.md docs/INSTALL.md docs/SELF_HOSTED_SERVER.md python-shim/tests/test_bootstrap_script.py
git commit -m "docs: add canonical bootstrap and doctor startup guidance"
```

---

### Task 4: Final Validation and Integration Gate

**Files:**

- Modify: none (validation only)
- **Step 1: Run Python test suites for touched areas**

Run: `python -m pytest python-shim/tests benchmarks/tests -v`  
Expected: PASS

- **Step 2: Run Rust engine tests**

Run: `cargo test --manifest-path rust-engine/Cargo.toml`  
Expected: PASS

- **Step 3: Run fast perf gate**

Run: `python benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2`  
Expected: exits 0 and updates perf matrix artifacts.

- **Step 4: Commit validation artifact updates if changed**

```bash
git add docs/perf_matrix_results.json docs/perf_matrix_summary.md
git commit -m "chore(benchmarks): refresh lite perf matrix baseline after batch1 changes"
```

- **Step 5: Prepare PR summary**

```markdown
## Summary
- Added Rust-first bootstrap command
- Added `ironflow_server.py doctor` diagnostics
- Updated canonical install/start docs

## Test plan
- [x] python -m pytest python-shim/tests benchmarks/tests -v
- [x] cargo test --manifest-path rust-engine/Cargo.toml
- [x] python benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2
```

---

## Spec Self-Review

### 1) Spec coverage

- Install reliability is covered by Task 1 and Task 3.
- One-command stack diagnostics are covered by Task 2 and Task 3.
- Trust signal baseline via perf validation is covered by Task 4.

No gap found for Batch 1 scope.

### 2) Placeholder scan

- No `TBD`, `TODO`, or unresolved placeholders remain.
- All tasks include explicit files, commands, and expected outcomes.

### 3) Type/signature consistency

- `bootstrap.main(argv)` is used consistently in tests and implementation snippet.
- `ironflow_server.main(["doctor"])` command contract is consistent across Task 2 and docs.

No naming conflicts found.

---

Plan complete and saved to `docs/superpowers/plans/2026-04-30-batch1-install-and-server-doctor.md`. Two execution options:

1. Subagent-Driven (recommended) - dispatch a fresh subagent per task with review between tasks.
2. Inline Execution - execute tasks in this session with checkpoints.

Which approach?