# Project IronFlow

[![CI](https://github.com/PPPSDavid/rust-based-prefect/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/PPPSDavid/rust-based-prefect/actions/workflows/ci.yml)
[![Docs](https://github.com/PPPSDavid/rust-based-prefect/actions/workflows/docs.yml/badge.svg?branch=main)](https://github.com/PPPSDavid/rust-based-prefect/actions/workflows/docs.yml)
[![License](https://img.shields.io/github/license/PPPSDavid/rust-based-prefect)](LICENSE)

Project IronFlow is a **hybrid MVP** that pairs a **Rust** orchestration control plane with **Prefect-style** Python authoring (`@flow` / `@task`). It is aimed at developers who already know **Prefect 3.x** and want to try a deterministic, locally persisted runner without adopting a new authoring model from scratch.

**Hosted docs (MkDocs):** [https://pppsdavid.github.io/rust-based-prefect/](https://pppsdavid.github.io/rust-based-prefect/) — start with [Prefect → IronFlow mapping](https://pppsdavid.github.io/rust-based-prefect/PREFECT_IRONFLOW_MAPPING/) after you enable GitHub Pages (see [RELEASING.md](RELEASING.md)).

| If you want… | Go to… |
| --- | --- |
| Map Prefect concepts to this repo | [docs/PREFECT_IRONFLOW_MAPPING.md](docs/PREFECT_IRONFLOW_MAPPING.md) |
| Supported behavior & gaps vs Prefect | [COMPATIBILITY.md](COMPATIBILITY.md) |
| Releases & version bumps | [RELEASING.md](RELEASING.md) |
| Change history | [CHANGELOG.md](CHANGELOG.md) |
| Agent / contributor workflow | [AGENTS.md](AGENTS.md) |

## Goals

- Improve orchestration robustness with a deterministic state machine and append-only event log.
- Keep Prefect-style `@flow` / `@task` Python authoring for a **prioritized subset** (import from **`prefect_compat`**, not `prefect`).
- Add static pre-run planning and forecasting for analyzable flow definitions.
- Keep performance-critical paths Rust-backed; use Python as the compatibility/API bridge.

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
| `rust-engine/` | Deterministic orchestration kernel (FSM, events, SQLite-backed reads). |
| `python-shim/` | `prefect_compat` package: decorators, runtime, optional FastAPI server. |
| `static-planner/` | Static graph IR and forecasting for analyzable flows. |
| `frontend/` | Optional Vite/React UI for runs and detail views. |
| `benchmarks/` | `perf_matrix.py`, Prefect vs IronFlow comparison harnesses. |
| `docs/` | Methodology notes, UI checklists, **MkDocs sources** for the site above. |
| `scripts/` | Server launcher (`ironflow_server.py`), seeds, demos. |

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

### 5. Optional: Rust query acceleration

```bash
cargo build --manifest-path rust-engine/Cargo.toml
```

The shim auto-detects the built `cdylib` under `rust-engine/target/`. Override with `IRONFLOW_RUST_LIB` if needed.

## Benchmarks

- **Prefect vs IronFlow A/B** (optional; needs Prefect installed):  
  `python benchmarks/compare_prefect_vs_ironflow.py` → writes `docs/perf_comparison.json` (JSON **array** — not for `perf_matrix.py compare`).
- **Deterministic control-plane matrix** (regression harness):  
  `python benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2`  
  See [docs/perf_methodology.md](docs/perf_methodology.md) and **Performance** in [AGENTS.md](AGENTS.md).

## Persistence defaults

- JSONL history: `data/ironflow_history.jsonl` or `IRONFLOW_HISTORY_PATH`
- SQLite read model: sidecar `.db` next to the JSONL path, or `data/ironflow_ui.db` when defaults apply — details in earlier sections of this file and in runtime code paths.

## End-to-end UI check

1. Start API + UI, then `python scripts/ui_e2e_seed.py`
2. Open `http://localhost:4173/runs` and verify tabs — see [docs/ui_e2e_visual_check.md](docs/ui_e2e_visual_check.md)

## Versioning & releases

- Single version in **`VERSION`**, kept in sync with Rust, Python packages, and `frontend/package.json` (`python scripts/check_version_sync.py`).
- Tag **`vX.Y.Z`** must match `VERSION`; see [RELEASING.md](RELEASING.md) and [CHANGELOG.md](CHANGELOG.md).

## Building docs locally

```bash
python -m pip install -r requirements-docs.txt
mkdocs serve
```

## Agent context

- [docs/MEMORY_BANK.md](docs/MEMORY_BANK.md) — session handoff notes  
- [AGENTS.md](AGENTS.md) — validation commands and perf matrix runbook  
