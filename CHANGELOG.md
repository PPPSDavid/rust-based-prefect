# Changelog

All notable changes to this project are documented here. Version numbers follow the repo-wide `VERSION` file (Python packages, Rust crate, and `frontend/package.json` stay in sync; see `RELEASING.md`).

## [Unreleased]

### Added

- **Docs SEO hygiene:** searchable home title/description (“Rust-based Prefect-style”), Open Graph / Twitter meta (`overrides/main.html`), generated `robots.txt` pointing at `sitemap.xml`, footer links to GitHub/PyPI, and Search Console steps in `RELEASING.md`.

### Changed

- **Project rename: IronFlow → FlowOxide (breaking).** Product, CLI, env vars, Rust crate/FFI symbols, Docker image names, and PyPI package names now use FlowOxide branding.
  - PyPI: `ironflow-prefect-compat` → **`flowoxide-prefect-compat`** (and `ironflow-static-planner` → **`flowoxide-static-planner`**). PyPI does not rename packages in place — publish the new names on the next release; the old names remain as historical uploads.
  - CLI: `ironflow` → **`flowoxide`**; manifest **`ironflow.yaml`** → **`flowoxide.yaml`**.
  - Environment: `IRONFLOW_*` → **`FLOWOXIDE_*`** (for example `FLOWOXIDE_HISTORY_PATH`, `FLOWOXIDE_RUST_LIB`, `FLOWOXIDE_API_URL`).
  - Rust: crate `ironflow-engine` → **`flowoxide-engine`**; cdylib / FFI `ironflow_*` → **`flowoxide_*`**.
  - Helpers: `scripts/ironflow_server.py` → **`scripts/flowoxide_server.py`**; docs mapping page renamed to `PREFECT_FLOWOXIDE_MAPPING.md`.
  - GitHub repository URL (`rust-based-prefect`) and Pages site path are unchanged for continuity/SEO.


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

[Unreleased]: https://github.com/PPPSDavid/rust-based-prefect/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/PPPSDavid/rust-based-prefect/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/PPPSDavid/rust-based-prefect/releases/tag/v0.1.2
[0.1.1]: https://github.com/PPPSDavid/rust-based-prefect/releases/tag/v0.1.1
[0.1.0]: https://github.com/PPPSDavid/rust-based-prefect/releases/tag/v0.1.0
