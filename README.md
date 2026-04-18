# Project IronFlow

[![CI](https://github.com/PPPSDavid/rust-based-prefect/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/PPPSDavid/rust-based-prefect/actions/workflows/ci.yml)
[![Docs](https://github.com/PPPSDavid/rust-based-prefect/actions/workflows/docs.yml/badge.svg?branch=main)](https://github.com/PPPSDavid/rust-based-prefect/actions/workflows/docs.yml)
[![Release](https://img.shields.io/github/v/release/PPPSDavid/rust-based-prefect?sort=semver)](https://github.com/PPPSDavid/rust-based-prefect/releases)
[![License](https://img.shields.io/github/license/PPPSDavid/rust-based-prefect)](LICENSE)

Project IronFlow is a **hybrid MVP** built around a **Rust orchestration kernel** (`rust-engine/`) and **Prefect-style** Python authoring (`@flow` / `@task` via `prefect_compat/`). It is aimed at developers who already know **Prefect 3.x** and want a deterministic, locally persisted control plane where **orchestration semantics live in Rust** and Python carries authoring and integration.

**Hosted docs (MkDocs):** [https://pppsdavid.github.io/rust-based-prefect/](https://pppsdavid.github.io/rust-based-prefect/) — the site is organized into **Get started**, **Concepts**, **How-to guides**, and **Reference** (see [docs/index.md](docs/index.md)); the [Prefect → IronFlow mapping](https://pppsdavid.github.io/rust-based-prefect/PREFECT_IRONFLOW_MAPPING/) remains the main Prefect-oriented entry. Enable GitHub Pages per [RELEASING.md](RELEASING.md).

| If you want… | Go to… |
| --- | --- |
| Layered doc home (hosted) | [docs/index.md](docs/index.md) |
| How Rust and Python fit together | [docs/architecture.md](docs/architecture.md) |
| **Install (current; hosted docs)** | [docs/INSTALL.md](docs/INSTALL.md) |
| PyPI / conda packaging roadmap (contributors) | [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) |
| Performance vs Prefect (expectations, caveats) | [docs/PERFORMANCE_OVERVIEW.md](docs/PERFORMANCE_OVERVIEW.md) |
| Quick start (demo flow, hosted docs) | [docs/QUICKSTART_DEMO.md](docs/QUICKSTART_DEMO.md) |
| Self-hosted server (API, workers, deployments, schedules; hosted docs) | [docs/SELF_HOSTED_SERVER.md](docs/SELF_HOSTED_SERVER.md) |
| Map Prefect concepts to this repo | [docs/PREFECT_IRONFLOW_MAPPING.md](docs/PREFECT_IRONFLOW_MAPPING.md) |
| Supported behavior & gaps vs Prefect | [COMPATIBILITY.md](COMPATIBILITY.md) |
| Releases & version bumps | [RELEASING.md](RELEASING.md) |
| Change history | [CHANGELOG.md](CHANGELOG.md) |
| Agent / contributor workflow | [AGENTS.md](AGENTS.md) |

## Goals

- **Rust (`rust-engine/`):** implement the orchestration **kernel** — deterministic state machine, transition validation, append-only history, and the native query/FFI surface the Python layer calls into. This is the primary control plane, not an add-on.
- **Python (`python-shim/`):** Prefect-style `@flow` / `@task` authoring and runtime glue for a **prioritized subset** (import from **`prefect_compat`**, not `prefect`), plus optional HTTP APIs.
- **Static planner:** graph IR and forecasting for analyzable flow definitions.
- **Performance:** hot paths belong in Rust by design; Python stays a thin compatibility and I/O bridge.

## Prefect baseline

- Target baseline: **Prefect OSS 3.x** (see `environment.yml` / `requirements-ci.txt` pins). For how Prefect itself describes flows and tasks, see the official [Prefect 3 get started](https://docs.prefect.io/v3/get-started) guide; upstream code is at [prefecthq/prefect](https://github.com/prefecthq/prefect).
- Compatibility is **subset-based** and validated against expectations in `COMPATIBILITY.md` — this is **not** a full Prefect runtime or Cloud substitute.

## Repository lifecycle

- This repository may start private for rapid iteration; CI, docs, and licensing are intended to make a **public fork-and-run** path straightforward.

## Licensing

- **Apache-2.0** — see [LICENSE](LICENSE). This is an independent prototype, not an official Prefect distribution.

## Layout

| Path | Role |
| --- | --- |
| **`rust-engine/`** | **Core** orchestration kernel: FSM, events, persistence hooks, FFI/cdylib for Python. |
| `python-shim/` | Authoring and runtime: `prefect_compat` decorators, control-plane calls into Rust, optional FastAPI server. |
| `static-planner/` | Static graph IR and forecasting for analyzable flows. |
| `frontend/` | Optional Vite/React UI for runs and detail views. |
| `benchmarks/` | `perf_matrix.py`, Prefect vs IronFlow comparison harnesses. |
| `docs/` | Methodology notes, UI checklists, **MkDocs sources** for the site above. |
| `scripts/` | Server launcher (`ironflow_server.py`), seeds, demos. |

See [docs/architecture.md](docs/architecture.md) for the runtime data path (Python → shim → Rust engine).

## Quickstart

### 1. Environment

**Conda (recommended; matches full stack):**

```bash
mamba env create -f environment.yml   # or: conda env create -f environment.yml
conda activate ironflow-dev
```

**pip only (CI parity, no conda):**

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows — on Unix: source .venv/bin/activate
python -m pip install -r requirements-ci.txt
```

Python **3.11+** is supported; `environment.yml` currently pins **3.12**.

### Using a numbered release (e.g. v0.1.1)

Pick **one** approach depending on whether you need the **full stack** (Rust engine sources, Python shim, benchmarks, scripts) or only the installable Python packages.

**A — Full checkout (recommended: `rust-engine` + shim + benchmarks + scripts; UI optional)**

```bash
git clone https://github.com/PPPSDavid/rust-based-prefect.git
cd rust-based-prefect
git checkout v0.1.1
```

Then follow the **Environment** subsection above (`environment.yml` or `requirements-ci.txt` at that tag). Run tests and scripts from the repo root as documented below.

**B — Install only the Python shim (`prefect_compat`) from git**

Use this when you want the **Python package alone** in another project. That install path does **not** ship the `rust-engine` crate or its native library; behavior falls back to Python-side implementations unless you **build `rust-engine` yourself** and point **`IRONFLOW_RUST_LIB`** at the resulting `cdylib`. For the intended architecture (kernel in Rust), prefer **full checkout** and `cargo build` below.

```bash
python -m pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@v0.1.1#subdirectory=python-shim"
```

The static planner package is optional and installs separately if you need it:

```bash
python -m pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@v0.1.1#subdirectory=static-planner"
```

Replace `v0.1.1` with the [latest release tag](https://github.com/PPPSDavid/rust-based-prefect/releases).

**Install guide (users):** see [docs/INSTALL.md](docs/INSTALL.md) on the hosted docs site. **PyPI wheels** are not published yet; future packaging work is described for maintainers in [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md).

**Docs vs releases:** The [hosted MkDocs site](https://pppsdavid.github.io/rust-based-prefect/) tracks the **`main`** branch. For documentation that exactly matches a tag, use GitHub’s file browser at that tag, or checkout the tag locally and run `mkdocs serve` (see **Building docs locally**).

### 2. Tests

From the repo root (`pytest.ini` sets `PYTHONPATH` for all packages):

```bash
python scripts/check_version_sync.py   # optional: verify VERSION ↔ artifacts
python -m pytest python-shim/tests static-planner/tests benchmarks/tests
cargo test --manifest-path rust-engine/Cargo.toml
```

### 3. Run flows (no server required)

Orchestration runs **in-process** through `prefect_compat`; the API and UI are optional. See [docs/PREFECT_IRONFLOW_MAPPING.md](docs/PREFECT_IRONFLOW_MAPPING.md) for import and wiring patterns, and `python-shim/tests/` for examples.

### 4. Optional API + UI

**One command** (backend + Vite dev server when `npm` is available):

```bash
python scripts/ironflow_server.py start
```

- API: `http://127.0.0.1:8000` — e.g. `GET /health`, `GET /api/flow-runs`
- UI: `http://localhost:4173` (typical Vite port; check script output)

Backend only:

```bash
python scripts/ironflow_server.py start --backend-only
```

Manual uvicorn (equivalent API):

```bash
python -m uvicorn python-shim.src.prefect_compat.server:app --host 127.0.0.1 --port 8000
```

### 5. Rust engine (build the native library)

The control plane is implemented in **`rust-engine/`**. Build the shared library so the Python shim can load it and use the Rust FSM, event pipeline, and native query path (auto-discovered under `rust-engine/target/`).

```bash
cargo build --manifest-path rust-engine/Cargo.toml
```

Override the search path with **`IRONFLOW_RUST_LIB`** if you build elsewhere. Skipping this step leaves you on Python fallbacks where implemented; for production-like behavior and parity with how the repo is developed, treat **`cargo build` as part of the normal full stack**, not a niche optimization.

## Benchmarks and expected speedup

IronFlow targets **control-plane** performance (state transitions, scheduling), not faster arbitrary Python in tasks. On the **synthetic A/B harness** checked into `docs/perf_comparison.json`, **in-process IronFlow** reaches on the order of **10⁴–10⁵ transitions/s** vs **10²–10¹** for local Prefect OSS on comparable toy flows — **roughly two to three orders of magnitude** higher throughput in that narrow scenario, and **~10²×** when IronFlow is used behind the local HTTP API. **End-to-end** pipelines dominated by I/O or heavy task bodies may see **much smaller** wall-clock gains.

Read the full caveats and tables in **[docs/PERFORMANCE_OVERVIEW.md](docs/PERFORMANCE_OVERVIEW.md)** (also on the hosted docs site).

- **Prefect vs IronFlow A/B** (optional; needs Prefect installed):  
  `python benchmarks/compare_prefect_vs_ironflow.py` → writes `docs/perf_comparison.json` (JSON **array** — not for `perf_matrix.py compare`).
- **Deterministic control-plane matrix** (IronFlow vs IronFlow regressions):  
  `python benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2`  
  See [docs/perf_methodology.md](docs/perf_methodology.md) and **Performance** in [AGENTS.md](AGENTS.md).

## Persistence defaults

- JSONL history: `data/ironflow_history.jsonl` or `IRONFLOW_HISTORY_PATH`
- SQLite read model: sidecar `.db` next to the JSONL path, or `data/ironflow_ui.db` when defaults apply — details in earlier sections of this file and in runtime code paths.

## End-to-end UI check

1. Start API + UI, then `python scripts/ui_e2e_seed.py`
2. Open `http://localhost:4173/runs` and verify tabs — see [docs/ui_e2e_visual_check.md](docs/ui_e2e_visual_check.md)

## Versioning & releases

- Current release: see GitHub [**Releases**](https://github.com/PPPSDavid/rust-based-prefect/releases) (e.g. **v0.1.1**). Single version in **`VERSION`**, kept in sync with Rust, Python packages, and `frontend/package.json` (`python scripts/check_version_sync.py`).
- Tag **`vX.Y.Z`** must match `VERSION`; pushing a tag creates a GitHub Release (see [RELEASING.md](RELEASING.md)). Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Building docs locally

The **GitHub Pages** site is **end-user** documentation: [docs/INSTALL.md](docs/INSTALL.md), quick start, architecture, compatibility, performance, etc. Maintainer topics such as [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) (PyPI roadmap) and [docs/perf_methodology.md](docs/perf_methodology.md) (benchmark harness internals) stay in the repository but are **not** published to the site.

```bash
python -m pip install -r requirements-docs.txt
mkdocs serve
```

## Agent context

- [docs/MEMORY_BANK.md](docs/MEMORY_BANK.md) — session handoff notes  
- [AGENTS.md](AGENTS.md) — validation commands and perf matrix runbook  
