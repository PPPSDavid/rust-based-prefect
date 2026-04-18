# Memory Bank

This file is a compact context handoff for future sessions.

## Project Snapshot

- Name: Project IronFlow (`rust-based-prefect`)
- Goal: Build a Prefect-compatible orchestration prototype with better determinism, performance, and static planning support.
- Status: MVP scaffold complete, private GitHub repo created, baseline benchmarks and persistence prototype implemented; deployment schedules now support interval + cron (with Rust-first scheduler paths when available).

## Core Architecture

- `rust-engine/`: deterministic state-machine kernel and append-only event model.
- `python-shim/`: Prefect-style ergonomics (`@flow`, `@task`, `submit`, `map`, `wait_for`) with compatibility runtime.
- `static-planner/`: static graph IR + forecast for supported flow subset.
- `benchmarks/`: performance comparisons (`ironflow`, `ironflow_http`, `prefect` local).

## Compatibility Baseline

- Prefect target: `3.x`
- Current scope: documented in `COMPATIBILITY.md`
- Known boundary: not full Prefect parity; subset-first with mixed dynamic fallback.

## Persistence Status

- IronFlow uses dual local persistence in shim runtime:
  - JSONL append history for durable event replay
  - SQLite read model for query/API/UI reads
- History includes flow/task creation and lifecycle events; read model includes runs, logs, events, and artifacts.
- Query hot paths can be Rust-backed through the optional Rust bridge.

## Performance Artifacts

- Methodology: `docs/perf_methodology.md`
- Latest output: `docs/perf_comparison.json`
- Benchmark script: `benchmarks/compare_prefect_vs_ironflow.py`

## Useful Commands

- Python tests: `python -m pytest python-shim/tests static-planner/tests`
- Rust tests: `cargo test --manifest-path rust-engine/Cargo.toml`
- Compare performance: `python benchmarks/compare_prefect_vs_ironflow.py`
- Generate forecast sample: `python scripts/run_forecast.py`

## Next High-Value Work

1. Move projection write hot paths from Python into Rust-backed implementation.
2. Expand Prefect API compatibility matrix with concrete parity tests.
3. Add migration/versioning path toward PostgreSQL for larger-scale persistence.
4. Add CI gates and benchmark regression thresholds.