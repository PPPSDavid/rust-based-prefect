# Changelog

All notable changes to this project are documented here. Version numbers follow the repo-wide `VERSION` file (Python packages, Rust crate, and `frontend/package.json` stay in sync; see `RELEASING.md`).

## [Unreleased]

## [0.1.1] — 2026-04-17

### Fixed

- CI on Linux: process-pool `task.map` tests now use a `prefect_compat` top-level callable (`mp_picklable.inc`) so multiprocessing can unpickle the task body reliably.
- `benchmarks/perf_matrix.py`: read-query phase calls `list_flow_runs`, `list_task_runs`, and `list_events` with the correct parameters (limits and `flow_run_id` UUIDs), fixing SQLite errors in CI perf runs.

### Documentation

- Documented how to **use a tagged release** (full repo checkout vs pip-installing the Python shim from git); see README and `RELEASING.md`.

## [0.1.0] — 2026-04-17

Initial public-oriented packaging: Apache-2.0 license, compatibility matrix, benchmarks, prototype UI, CI, MkDocs site, and Prefect→IronFlow mapping.

[Unreleased]: https://github.com/PPPSDavid/rust-based-prefect/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/PPPSDavid/rust-based-prefect/releases/tag/v0.1.1
[0.1.0]: https://github.com/PPPSDavid/rust-based-prefect/releases/tag/v0.1.0
