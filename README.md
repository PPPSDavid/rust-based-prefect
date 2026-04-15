# Project IronFlow

Project IronFlow is a hybrid MVP that explores a Rust-based orchestration control plane with Prefect-compatible Python authoring.

## Goals

- Improve orchestration robustness with a deterministic state machine and append-only event log.
- Keep existing Prefect-style `@flow` / `@task` Python authoring for a prioritized subset.
- Add static pre-run planning and forecasting for analyzable flow definitions.

## Prefect Baseline

- Target baseline: **Prefect OSS `3.x`**.
- Compatibility target is validated against a pinned baseline in `COMPATIBILITY.md`; dev env allows modern `3.x`.
- See `COMPATIBILITY.md` for exact supported behavior and subset boundaries.

## Repository Lifecycle

- This repository starts **private** for rapid internal iteration.
- If benchmark and robustness gates are met, it can be published with OSS-ready docs and attribution.

## Licensing

- This project is released under **Apache-2.0** (`LICENSE`).
- It is an independent prototype and is not an official Prefect distribution.

## Layout

- `rust-engine/` deterministic orchestration kernel and API.
- `python-shim/` compatibility adapter for Prefect-style Python flows/tasks.
- `static-planner/` static graph extraction and forecasting tooling.
- `docs/` compatibility and benchmark baselines.
- `scripts/` benchmark / demo scripts.

## Quickstart

1. Create isolated environment:
   - `mamba env create -f environment.yml` (preferred), or
   - `conda env create -f environment.yml`
2. Activate:
   - `conda activate ironflow-dev`
3. Run Python tests:
   - `python -m pytest python-shim/tests static-planner/tests`
4. Run Rust tests:
   - `cargo test --manifest-path rust-engine/Cargo.toml`
5. Run performance comparison:
   - `python benchmarks/compare_prefect_vs_ironflow.py`
   - Compares `ironflow` (in-process), `ironflow_http` (server boundary), and local `prefect`.
   - Results are written to `docs/perf_comparison.json`

## History Persistence

- IronFlow now supports local durable history via JSONL append log.
- Set `IRONFLOW_HISTORY_PATH` or pass `history_path` when creating `InMemoryControlPlane`.
- Persisted data includes flow creation, flow transitions, task creation, and task events.
- Current status: durable local history is available; a full Prefect-equivalent relational history model is a future step.
