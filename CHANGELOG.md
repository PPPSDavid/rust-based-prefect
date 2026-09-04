# Changelog

All notable changes to this project are documented here. Version numbers follow the repo-wide `VERSION` file (Python packages, Rust crate, and `frontend/package.json` stay in sync; see `RELEASING.md`).

## [Unreleased]

### Added

- **CPython 3.13 and 3.14 GIL wheels** on the same platforms as 3.11/3.12 (Linux x86_64 + aarch64, Windows, macOS). `requires-python` stays `>=3.11`.
- **`scripts/check_python_support_matrix.py`:** CI fails when a still-supported CPython minor matching `requires-python` is missing from test/wheel matrices (endoflife.date + `scripts/python_support_snapshot.json` fallback).
- **Experimental CPython 3.14t:** non-blocking CI job and optional TestPyPI Linux `cp314t` wheel (`include_freethreaded`); not uploaded to production PyPI. CPU-bound `ThreadPoolTaskRunner` microbench preset `cpu_task`.

## [0.3.0] — 2026-08-30

Quality and structure release. Public `prefect_compat` APIs, `COMPATIBILITY.md` claims, and `perf_matrix` workload shapes are unchanged. Optional measured hot-path speedups (remaining Python projection writes → Rust `ui_write`, Postgres schedule/gate in Rust) were **not** bundled: there was no isolated candidate that could be compared against a same-machine frozen lite baseline.

### Added (shipped on `main` after 0.2.0)

- **Self-hosted stack:** server Docker image (Tier A), Basic auth (Tier C), SQLite store extract (B0), Postgres via `IRONFLOW_DATABASE_URL` (B1), HTTP worker claim/started/finished (B2), Compose + `ironflow server services start` (B3/B5).
- **Global and tag concurrency limits:** Rust slot ledger, `concurrency` / `rate_limit`, CLI `ironflow gcl`, UI Concurrency page.
- **Flow-run final state:** default `wait_all` aggregation in Rust (`resolve_flow_terminal_state`) with `detach` / `@flow(final_state="explicit")` escapes.
- **Task resume (Goal A):** skip eligible `COMPLETED` nodes on retry; `@task(persist_result=True)` JSON-safe payloads.
- **Lifecycle control:** cancel (terminate), pause `mode=drain|terminate`, operator resume; process-pool SIGTERM→SIGKILL; run-detail UI.
- **Concurrent `task.submit`:** `ThreadPoolTaskRunner` runs independent submits in parallel.
- **Airtight concurrency harness:** `pytest -m airtight` for overlapping-state invariants.

### Changed (this series)

- **Quality gates:** `rustfmt` + clippy `-D warnings`, `ruff format`, ruff `C901` (max 20), clippy `too_many_lines` (120), frontend `tsc` build in CI, dedicated Linux wheel job (split from Postgres), `scripts/code_metrics.py` file-LOC ratchet, `CONTRIBUTING.md` + `scripts/lint.sh`.
- **Python control plane:** `runtime.py` is a mixin facade (`InMemoryControlPlane` public alias unchanged). Logic lives in `prefect_compat/control_plane/`. FastAPI routers in `routes/`; demo flows in `flow_registry.py`. Optional extra `server` (`fastapi`, `uvicorn`).
- **Rust crate:** C ABI `ironflow_*` unchanged. FFI dispatch split under `ffi/`; `deployment_ops/` and `concurrency_ops/` are packages. No `rust-engine/src` file exceeds 800 lines.
- **Docs:** Get started nav is four entries; published pages use GitHub blob URLs instead of excluded `docs/plans/**`; architecture module map; historical perf dumps under `docs/archive/perf/`.

### Metrics (falsifiable)

Production files **>1000 LOC** in `python-shim/src` + `rust-engine/src`:

| When | Count | Files |
| --- | ---: | --- |
| MR1 ratchet baseline | **4** | `runtime.py`, `decorators.py`, `deployment_ops.rs`, `ffi.rs` |
| After MR3 | **1** | `decorators.py` (1427); `benchmarks/perf_matrix.py` remains parked |

Allowlisted files must not grow. New production files must stay ≤800 lines.

## [0.2.0] — 2026-07-12

### Added

- **Deployments (Tier 1):** `ironflow init`, `ironflow deploy`, `ironflow serve`, and `ironflow worker start`; manifest file **`ironflow.yaml`**; Python **`prefect_compat.deploy.deploy()`** and **`serve()`**; standalone workers sharing **`IRONFLOW_HISTORY_PATH`** with the API.
- **Deployment schedules:** interval, optional **cron**, and a Rust-preferred **RRule subset** (`FREQ=MINUTELY|HOURLY|DAILY|WEEKLY`); schedule ticks and claim waits prefer the Rust engine when available.
- **Subflows (subset):** blocking **inline** nested `@flow` calls and **deployment-backed** subflows via `deployment_ref(...).submit()` with `SubflowFuture`, `wait_for`, and parent cancel propagation; static planner and UI DAG node kinds `inline_subflow` / `subflow_task`.
- **Temporal gate tasks (IronFlow extension):** `gate(name=..., max_wait=...)` with `.submit(until=..., wait_for=[...])` for in-flow calendar barriers; default **`max_wait`** safeguard of one day; UI node kind **`gate_task`**.
- **Prefect-like web UI:** runs, deployments, work pools, cancel/retry flows, and E2E cancel-retry workflow; static planner DAG zoom/search and path highlight.
- **Bootstrap diagnostics:** `scripts/bootstrap.py --native-check` for PyPI users with actionable failure hints.
- **Benchmarks:** `flow_map` preset for thread-pool map workloads; subflow perf_matrix recipes; multicore read-pool and concurrency benchmarks.

### Changed

- **Performance:** Rust read pool, per-handle locks, forecast caching, active-flow submit optimizations, and serialized map control-plane improvements.
- **CI / dev:** Phase 1 **uv workspace** hub (lock, CI, Cloud); **ruff** and **ty** validation gates; cross-OS PR lite perf matrix; portable **code-review-graph** agent setup.
- **Documentation:** PyPI-first install story across README, INSTALL, MkDocs nav, reference layer, subflows/deployments/how-to guides, task-runner guide, and compatibility review workflow.

### Fixed

- Static planner flow parsing edge cases; frontend schedule field typing.

## [0.1.2] — 2026-05-02

### Added

- **PyPI wheels for CPython 3.12** alongside **3.11** on Linux x86_64, Windows, and macOS.
- **Linux aarch64:** **cp311** and **cp312** wheels from a single **`cibuildwheel`** run on the self-hosted ARM64 runner.

### Documentation

- README, **INSTALL**, **DISTRIBUTION**, and **COMPATIBILITY** updated for the published wheel matrix and Python-version expectations.

## [0.1.1] — 2026-04-17

### Fixed

- CI on Linux: process-pool `task.map` tests now use a `prefect_compat` top-level callable (`mp_picklable.inc`) so multiprocessing can unpickle the task body reliably.
- `benchmarks/perf_matrix.py`: read-query phase calls `list_flow_runs`, `list_task_runs`, and `list_events` with the correct parameters (limits and `flow_run_id` UUIDs), fixing SQLite errors in CI perf runs.

### Documentation

- Documented how to **use a tagged release** (full repo checkout vs pip-installing the Python shim from git); see README and `RELEASING.md`.

## [0.1.0] — 2026-04-17

Initial public-oriented packaging: Apache-2.0 license, compatibility matrix, benchmarks, prototype UI, CI, MkDocs site, and Prefect→IronFlow mapping.

[Unreleased]: https://github.com/PPPSDavid/rust-based-prefect/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/PPPSDavid/rust-based-prefect/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/PPPSDavid/rust-based-prefect/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/PPPSDavid/rust-based-prefect/releases/tag/v0.1.2
[0.1.1]: https://github.com/PPPSDavid/rust-based-prefect/releases/tag/v0.1.1
[0.1.0]: https://github.com/PPPSDavid/rust-based-prefect/releases/tag/v0.1.0
