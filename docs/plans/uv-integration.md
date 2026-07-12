# uv Integration Plan (IronFlow)

**Status:** Proposed  
**Last updated:** 2026-07-12  
**Scope:** root tooling (`pyproject.toml`, `uv.lock`), CI/Cloud install scripts, contributor docs  
**Forbidden (this plan):** changing wheel native-build semantics, conda-forge feedstock, frontend npm toolchain, Rust crate layout  
**Related:** [DISTRIBUTION.md](../DISTRIBUTION.md) · [RELEASING.md](../../RELEASING.md) · [INSTALL.md](../INSTALL.md) · [AGENTS.md](../../AGENTS.md)

> **For agentic workers:** implement **Phase 1** first (workspace + lock + CI/Cloud). Phases 2–3 are follow-up PRs. Steps use checkbox (`- [ ]`) syntax for tracking. Prefer `superpowers:subagent-driven-development` or `superpowers:executing-plans` when executing.

**Goal:** Make **uv** the primary Python dependency and env tool for **developing and testing** IronFlow, without replacing PyPI consumer installs (`pip` / `uv pip`), Cargo, cibuildwheel, or optional conda.

**Architecture:** Add a **root uv workspace** whose members are `python-shim/` and `static-planner/`. Commit a single `uv.lock`. Point CI and Cursor Cloud at `uv sync --frozen`. Keep packaging backends (setuptools + `build_native.py`) and publish workflows unchanged in Phase 1. Phase 2 improves release hygiene; Phase 3 optionally trials `uv build` / conda-forge.

**Tech stack:** [uv](https://docs.astral.sh/uv/), existing setuptools/`python -m build`, cibuildwheel, auditwheel, Cargo, npm.

---

## 1. Problem statement

Today IronFlow has **three overlapping Python env stories** and **no lockfile**:

| Path | Mechanism | Pain |
| --- | --- | --- |
| CI / Cloud / many docs | `pip install -r requirements-ci.txt` | Slow; non-reproducible ranges; PEP 668 friction on Cloud |
| Desktop “full stack” | `environment.yml` (conda/mamba) | Parallel truth vs CI; Prefect only on conda pip-extra |
| End users | `pip` / `uv pip install ironflow-prefect-compat` | Fine — already documented |

Release cost is dominated by the **native wheel matrix** (cargo → cdylib → auditwheel/cibuildwheel), not by Python resolver speed. uv will not remove that work; it **will** speed and harden **dev/CI installs**.

Docs already advertise `uv pip install` for consumers. The gap is **integrating uv into the library’s own development and release *hub*** — workspace, lockfile, CI, Cloud — not pretending uv replaces Cargo or PyPI.

---

## 2. Design principles (consensus)

1. **uv for hub, not exclusivity** — primary tool for contributors and CI; consumers keep `pip` + `uv pip`.
2. **One production distribution channel** — PyPI wheels. Do not run pip/uv/conda as co-equal *release* paths.
3. **conda stays optional** — `environment.yml` for contributors who want Rust+Python in one env; not required for CI parity.
4. **Do not rewrite native packaging in Phase 1** — keep setuptools, `setup.py` cmdclass, `build_native.py`, auditwheel, cibuildwheel.
5. **Lock what CI runs** — committed `uv.lock`; CI uses `--frozen`.
6. **Preserve `pytest.ini` pythonpath** — root tests may keep path-based imports; editable workspace installs are additive, not mandatory for every agent command.
7. **Version sync stays multi-artifact** — `uv version` alone cannot bump `VERSION`, `Cargo.toml`, and `frontend/package.json`.

---

## 3. Where uv fits (and where it does not)

```text
┌─────────────────────────────────────────────────────────────┐
│  IronFlow monorepo                                          │
│                                                             │
│  uv workspace (NEW)                                         │
│    members: python-shim, static-planner                     │
│    dependency-groups.dev ← today’s requirements-ci.txt      │
│    uv.lock (committed)                                      │
│                                                             │
│  Cargo ──────── rust-engine (unchanged)                     │
│  npm ────────── frontend (unchanged)                        │
│  setuptools ─── wheel build + native stage (unchanged P1)   │
│  cibuildwheel ─ aarch64 / manylinux (unchanged P1)          │
└─────────────────────────────────────────────────────────────┘
        │                         │
        ▼                         ▼
   Dev / CI / Cloud          Consumers
   uv sync --frozen          pip install …
                             uv pip install …
                             (optional) conda env create
```

| Concern | Integrate uv? | Notes |
| --- | --- | --- |
| Contributor / CI / Cloud Python deps | **Yes (Phase 1)** | Root workspace + lock + sync |
| Editable local packages | **Yes (Phase 1)** | Workspace members |
| Consumer install docs | **Already partial** | Keep both `pip` and `uv pip` |
| Wheel build matrix | **No (Phase 1)** | Keep `python -m build` / cibuildwheel |
| PyPI upload | **No (Phase 1)** | Keep `gh-action-pypi-publish` |
| Version bump across artifacts | **Phase 2 script** | May call `uv version` for pyprojects only |
| conda-forge | **Phase 3 optional** | Separate feedstock effort |
| Frontend / Rust | **Never** | Wrong tool |

---

## 4. Target layout (Phase 1)

### Files created

| Path | Role |
| --- | --- |
| `pyproject.toml` (repo root) | Workspace root: `tool.uv.workspace`, `dependency-groups`, `requires-python` |
| `uv.lock` | Locked resolution for CI/Cloud/devs |
| `.python-version` | Pin default interpreter (e.g. `3.12`) for local `uv` |

### Files modified

| Path | Role |
| --- | --- |
| `.github/workflows/ci.yml` | `astral-sh/setup-uv` + `uv sync --frozen --group dev` for Python jobs |
| `.cursor/cloud-install.sh` | Install uv if needed; `uv sync --frozen` instead of bare pip for app deps |
| `requirements-ci.txt` | Header pointing at uv lock; keep as export *or* thin shim for a transition period |
| `README.md`, `docs/INSTALL.md`, `docs/how-to/setup.md`, `AGENTS.md` | Dev path: prefer `uv sync` |
| `docs/DISTRIBUTION.md`, `RELEASING.md` | Document hub vs consumer roles |
| `docs/plans/README.md` | Link this plan |

### Files intentionally untouched (Phase 1)

- `python-shim/setup.py`, `python-shim/build_native.py`, `python-shim/pyproject.toml` `[build-system]`
- `.github/workflows/publish-pypi.yml`, `publish-testpypi.yml` (wheel steps)
- `environment.yml` (optional path; optional footnote only)
- `frontend/**`, `rust-engine/**` sources

### Example root `pyproject.toml` (authoritative sketch)

```toml
[project]
name = "ironflow-workspace"
version = "0.0.0"
description = "IronFlow monorepo workspace root (not published to PyPI)."
requires-python = ">=3.11"
dependencies = []

[dependency-groups]
dev = [
  "pytest>=8,<9",
  "pydantic>=2,<3",
  "fastapi>=0.115,<1",
  "httpx>=0.27,<1",
  "uvicorn>=0.30,<1",
  "networkx>=3,<4",
  "pyyaml>=6,<7",
  "rich>=13,<14",
  "ruff>=0.8,<1",
]

[tool.uv.workspace]
members = ["python-shim", "static-planner"]

[tool.uv]
package = false
```

Notes:

- Root is **`package = false`** so it is not published; PyPI package remains `ironflow-prefect-compat` from `python-shim/`.
- Mirror ranges from current `requirements-ci.txt` exactly at first lock, then let the lock pin transitive versions.
- Do **not** add Prefect to the default `dev` group unless CI already needs it for a specific job (today `requirements-ci.txt` does not include Prefect; only `environment.yml` does via pip extra).

---

## 5. Phased delivery

| Phase | Outcome | Risk | Separate PR? |
| --- | --- | --- | --- |
| **1 — Hub** | Workspace + `uv.lock` + CI/Cloud use `uv sync --frozen` | Low | Yes (first) |
| **2 — Release hygiene** | Version bump script; optional publish reuse of CI wheel artifacts | Medium | Yes |
| **3 — Optional stretch** | Trial `uv build` on one Linux job; decide conda-forge | Medium–high | Yes, only if justified |

**Explicit non-goals for Phase 1:** switching publish to `uv publish`; maturin migration; deleting conda; dropping `pip` from consumer docs.

---

## 6. Implementation tasks

### Task 1: Root workspace manifest

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Modify: none yet

- [ ] **Step 1: Create branch** (if not already on a feature branch)

```bash
git checkout main && git pull origin main
git checkout -b feat/uv-workspace-hub
```

- [ ] **Step 2: Write root `pyproject.toml`**

Use the sketch in §4. Confirm `python-shim/pyproject.toml` and `static-planner/pyproject.toml` already have `[project]` + `[build-system]` so they are valid workspace members.

- [ ] **Step 3: Write `.python-version`**

```text
3.12
```

Matches `environment.yml` default; CI matrix still tests 3.11 and 3.12 via `UV_PYTHON` / setup-uv.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .python-version
git commit -m "chore: add root uv workspace manifest"
```

---

### Task 2: Generate and commit `uv.lock`

**Files:**
- Create: `uv.lock`
- Modify: `requirements-ci.txt` (deprecation header)

- [ ] **Step 1: Install uv locally (or in CI dry-run)**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

Expected: a recent uv 0.x printed.

- [ ] **Step 2: Lock the workspace**

```bash
uv lock
```

Expected: `uv.lock` created; resolves `python-shim`, `static-planner`, and `dev` group deps.

- [ ] **Step 3: Sync and smoke-test**

```bash
uv sync --group dev
uv run pytest python-shim/tests static-planner/tests benchmarks/tests -q --collect-only
```

Expected: collection succeeds (may need `cargo build` for some native tests — same as today).

- [ ] **Step 4: Annotate `requirements-ci.txt`**

Prepend:

```text
# DEPRECATED for primary installs: prefer `uv sync --frozen --group dev` (see docs/plans/uv-integration.md).
# Kept for transitional pip-only environments. Ranges should stay aligned with [dependency-groups].dev
# in the root pyproject.toml.
```

Do **not** delete the file in Phase 1.

- [ ] **Step 5: Commit**

```bash
git add uv.lock requirements-ci.txt
git commit -m "chore: commit uv.lock for reproducible Python deps"
```

---

### Task 3: Wire CI Python jobs to uv

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Replace pip install in `python-rust` job**

After `actions/checkout@v4`, use:

```yaml
- uses: astral-sh/setup-uv@v6
  with:
    enable-cache: true
- name: Sync Python deps
  run: uv sync --frozen --group dev --python ${{ matrix.python-version }}
- uses: dtolnay/rust-toolchain@stable
- name: Python tests
  run: uv run pytest python-shim/tests static-planner/tests benchmarks/tests
- name: Rust tests
  run: cargo test --manifest-path rust-engine/Cargo.toml
```

Remove the `actions/setup-python` + `pip install -r requirements-ci.txt` block from this job (setup-uv manages Python).

- [ ] **Step 2: Update `frontend-e2e` backend deps the same way**

```yaml
- uses: astral-sh/setup-uv@v6
  with:
    enable-cache: true
- name: Sync Python deps
  run: uv sync --frozen --group dev --python 3.11
```

Keep `actions/setup-node` and Playwright steps unchanged. Run E2E with `uv run` only if the job invokes Python entrypoints that need the venv on `PATH`; otherwise activate via `uv run` wrappers as needed.

- [ ] **Step 3: Leave wheel jobs on pip/`python -m build` for Phase 1**

Do not change `wheel-linux`, `wheel-windows`, `wheel-macos`, or `wheel-linux-aarch64` packaging tool installs yet.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: use uv sync --frozen for Python test jobs"
```

---

### Task 4: Cursor Cloud install script

**Files:**
- Modify: `.cursor/cloud-install.sh`
- Modify: `AGENTS.md` (Cursor Cloud section — command examples)

- [ ] **Step 1: Install uv in cloud-install, then sync**

Replace the `pip install -r requirements-ci.txt` block with something equivalent to:

```bash
echo "[cloud-install] Ensuring uv ..."
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

echo "[cloud-install] Python deps (uv sync --frozen) ..."
uv sync --frozen --group dev
```

Keep `npm --prefix frontend ci`, `cargo build`, and CRG setup. CRG may still use pip/`requirements-agent.txt` (isolated tooling) unless you deliberately migrate that later.

- [ ] **Step 2: Update AGENTS.md Cloud notes**

Document that agents should prefer:

```bash
uv sync --frozen --group dev
uv run pytest python-shim/tests static-planner/tests benchmarks/tests
```

Keep `python3 -m pytest` as a fallback when the venv is already synced and on `PATH`.

- [ ] **Step 3: Commit**

```bash
git add .cursor/cloud-install.sh AGENTS.md
git commit -m "chore(cloud): install Python deps with uv sync"
```

---

### Task 5: Contributor and distribution docs

**Files:**
- Modify: `README.md` (Quickstart § Environment)
- Modify: `docs/INSTALL.md` / `docs/how-to/setup.md`
- Modify: `docs/DISTRIBUTION.md`
- Modify: `RELEASING.md` (short “tooling” note)
- Modify: `docs/plans/README.md`

- [ ] **Step 1: README Quickstart — lead with uv**

Recommended order:

1. **uv (recommended for contributors):** `uv sync --group dev` then `cargo build`
2. **pip / venv (transition):** `requirements-ci.txt`
3. **conda (optional):** `environment.yml`

Keep consumer **PyPI** section with both `pip` and `uv pip`.

- [ ] **Step 2: DISTRIBUTION.md — add “Dev hub vs release channel”**

State explicitly:

- **Dev hub:** uv workspace + lock
- **Release channel:** PyPI wheels (pip/uv are installers)
- **Optional:** conda for local full-stack only; conda-forge still a follow-up

- [ ] **Step 3: Link this plan from `docs/plans/README.md`**

| Plan | Status |
| --- | --- |
| [uv-integration.md](uv-integration.md) | Proposed (Phase 1 next) |

- [ ] **Step 4: Commit**

```bash
git add README.md docs/INSTALL.md docs/how-to/setup.md docs/DISTRIBUTION.md RELEASING.md docs/plans/README.md
git commit -m "docs: document uv as primary contributor Python toolchain"
```

---

### Task 6: Validate Phase 1

- [ ] **Step 1: Local validation**

```bash
uv sync --frozen --group dev
cargo build --manifest-path rust-engine/Cargo.toml
uv run pytest python-shim/tests static-planner/tests benchmarks/tests
cargo test --manifest-path rust-engine/Cargo.toml
```

Expected: same pass profile as AGENTS.md Expected Validation.

- [ ] **Step 2: Confirm wheel path still works without uv lock**

```bash
cd python-shim && python -m pip install build && python -m build --wheel
```

Expected: wheel builds (needs cargo) — proves Phase 1 did not couple packaging to the workspace lock.

- [ ] **Step 3: Push and open PR for Phase 1 only**

PR body should link `docs/plans/uv-integration.md` and list Phase 2/3 as out of scope.

---

## 7. Phase 2 — Release hygiene (follow-up PR)

Do **not** mix into Phase 1.

### 7.1 Unified version bump

**Create:** `scripts/bump_version.py` (or extend `scripts/check_version_sync.py`)

Behavior:

1. Read or accept new semver (`0.1.3`)
2. Write `VERSION`
3. Update `rust-engine/Cargo.toml` `[package].version`
4. Update `python-shim/pyproject.toml` and `static-planner/pyproject.toml`
5. Update `frontend/package.json` `version`
6. Run `scripts/check_version_sync.py` and exit non-zero on mismatch

Optional: call `uv version <ver> --package ironflow-prefect-compat` / static-planner for pyprojects only — still must update Cargo + frontend + `VERSION`.

### 7.2 Deduplicate wheel builds (optional, higher risk)

Today CI and both publish workflows rebuild the same matrix.

Target shape:

1. CI continues to build + smoke-install + upload artifacts on `main`/PRs
2. Publish workflows on `workflow_dispatch` either:
   - rebuild (current, safer), or
   - download artifacts from a selected successful CI run / tag build job

Prefer a dedicated `build-wheels.yml` `workflow_call` shared by CI and publish before inventing artifact cross-workflow coupling.

### 7.3 `static-planner` publish decision

Document whether `ironflow-static-planner` remains git-subdirectory-only or gets a publish job. No silent PyPI upload without an explicit decision in `RELEASING.md`.

---

## 8. Phase 3 — Optional stretch

| Idea | When to do it | Exit criteria |
| --- | --- | --- |
| Trial `uv build` on Linux x86_64 CI only | After Phase 1 stable | Same wheel tag + `native_library_available()` smoke |
| Switch publish to `uv publish` | Only if OIDC story is equal to current action | Dry-run + TestPyPI success |
| conda-forge feedstock | Real user demand | Separate plan; vendors same cdylib story |
| maturin | Only if ctypes packaging becomes a liability | Separate architecture review |

---

## 9. Acceptance criteria

### Phase 1 done when

- [ ] Root `pyproject.toml` + committed `uv.lock` exist
- [ ] `uv sync --frozen --group dev` installs CI-equivalent deps
- [ ] `ci.yml` Python test / E2E backend steps use uv
- [ ] `.cursor/cloud-install.sh` uses uv for app Python deps
- [ ] Docs describe uv as primary **contributor** path; pip+uv pip remain **consumer** paths
- [ ] Wheel publish workflows and setuptools native build **unchanged**
- [ ] AGENTS.md Expected Validation still passes (via `uv run` or synced venv)

### Phase 1 non-regressions

- `scripts/check_version_sync.py` still green
- `python -m build --wheel` in `python-shim/` still works with cargo
- `environment.yml` still valid for optional conda users

---

## 10. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Agents/docs still say `pip install -r requirements-ci.txt` | Keep file + header; update AGENTS/README in same PR |
| setup-uv version pin drifts | Pin `astral-sh/setup-uv@v6` (or current major) in workflows |
| Workspace wants to build packages on sync and trips native build | Prefer `UV_NO_SYNC`/`--no-install-workspace` only if needed; or set `IRONFLOW_SKIP_NATIVE_BUILD=1` for pure-Python editable sync and rely on repo `cargo build` + `pytest.ini` pythonpath |
| Lockfile churn in PRs | Accept for dep PRs; regenerate with `uv lock` only when intentional |
| Self-hosted aarch64 PEP 668 | Wheel jobs stay on venv+pip in Phase 1 |

---

## 11. Decision record (three-expert consensus)

**Question:** Integrate uv exclusively, or alongside pip/conda?

1. **Packaging engineer:** Exclusive uv would break consumer expectations and does not replace cibuildwheel; use uv for hub only.
2. **Release engineer:** Multiple release channels (pip+uv+conda+forge) multiply failure modes; one PyPI channel.
3. **Contributor UX:** Lockfile + fast sync is the highest ROI; conda remains a convenience, not a second source of truth for CI.

**Consensus:** Phase 1 uv workspace/lock/CI/Cloud; keep pip for consumers; conda optional; defer publish/tooling experiments to later phases.

---

## 12. Execution handoff

**Phase 1 implementation options:**

1. **Subagent-driven** — one subagent per Task 1–6, review between tasks  
2. **Inline** — single session executing Tasks 1–6 with commits after each task  

Open the Phase 1 PR from a branch named like `feat/uv-workspace-hub` (or `cursor/uv-workspace-hub-<id>` on Cloud). Link this plan in the PR body. Do not start Phase 2 until Phase 1 is merged and CI is green on `main`.
