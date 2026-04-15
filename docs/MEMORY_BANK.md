# Memory Bank

This file is a compact context handoff for future sessions.

## Project Snapshot

- Name: Project IronFlow (`rust-based-prefect`)
- Goal: Build a Prefect-compatible orchestration prototype with better determinism, performance, and static planning support.
- Status: MVP scaffold complete, private GitHub repo created, baseline benchmarks and persistence prototype implemented.

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

- IronFlow supports local JSONL history persistence in shim runtime.
- History includes flow/task creation and lifecycle events.
- This is durable local state, not yet full relational event store parity with Prefect.

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

1. Add read-model API shaped for UI consumption.
2. Expand Prefect API compatibility matrix with concrete parity tests.
3. Replace JSONL persistence with DB-backed event store.
4. Add CI gates and benchmark regression thresholds.