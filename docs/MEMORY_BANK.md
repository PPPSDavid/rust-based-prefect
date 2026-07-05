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

## Run lifecycle: cancel / retry (current vs desired)

**Current behavior (MVP — do not change without explicit task):**

- **Cancel** (`POST /api/flow-runs/{id}/cancel`): sets flow run state to `CANCELLED` and marks in-flight task runs `CANCELLED` in the control plane / SQLite read model. Long-running task bodies are not cooperatively interrupted unless they poll cancellation themselves (no default hook yet).
- **Retry** (`POST /api/flow-runs/{id}/retry`): for deployment-backed runs, calls `trigger_deployment_run` with the same deployment and parameters → **new deployment run → new flow run → full flow re-execution from scratch**. This is **not** Prefect task-resume parity.

**Known gap (documented, future work):**

- For multi-task flows where some tasks **completed** before cancel, **retry currently recomputes those completed tasks**. Desired Prefect-like semantics: on retry, **already-completed tasks should not be recomputed** (task-level resume / result cache keyed by flow run lineage or equivalent).
- Implementing this requires architectural work: task result persistence across retry, idempotent resume graph, and UI/API surfacing of which tasks were skipped vs re-run. Track in compatibility matrix before claiming parity.

**Useful test scenario (manual / E2E):**

- Flow: fast task → `sleep` ~10s task → downstream task. Trigger → cancel while sleeping → retry → wait for completion. Today, expect all tasks to run again on retry; use this to validate when resume lands.

## Next High-Value Work

1. Move projection write hot paths from Python into Rust-backed implementation.
2. Expand Prefect API compatibility matrix with concrete parity tests.
3. Add migration/versioning path toward PostgreSQL for larger-scale persistence.
4. Add CI gates and benchmark regression thresholds.
5. **Task-level resume on flow-run retry** (skip recomputation of tasks that completed before cancel) — see section above.