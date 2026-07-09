# Compatibility Matrix

This document tracks compatibility targets against Prefect OSS.

Maintainers should use `docs/compatibility_review_workflow.md` before changing this matrix or choosing a new Prefect-alignment feature. The workflow keeps upstream comparison, gap selection, documentation, and implementation decisions tied together.

## Baseline

- Upstream project: `prefecthq/prefect` (self-hosted OSS context)
- Baseline major/minor: `3.x`
- Initial validation target: `3.0.0`

## Python versions & PyPI (`ironflow-prefect-compat`)

- **`requires-python`:** `>=3.11` (see `python-shim/pyproject.toml`).
- **Prebuilt wheels:** CI publishes **manylinux** (x86_64 + aarch64), **Windows** (`win_amd64`), and **macOS** wheels for **CPython 3.11 and 3.12**. Confirm exact filenames on [PyPI → Download files](https://pypi.org/project/ironflow-prefect-compat/#files).
- **Other CPython versions** (for example 3.13): may resolve to **sdist** or fail until wheels exist — build from a **full checkout** with **`cargo build`** and/or set **`IRONFLOW_RUST_LIB`** per the hosted [Installation](https://pppsdavid.github.io/rust-based-prefect/INSTALL/) guide (see also [`docs/INSTALL.md` on GitHub](https://github.com/PPPSDavid/rust-based-prefect/blob/main/docs/INSTALL.md)).

## Phase 1 runtime compatibility (current MVP target)

- Supported:
  - `@flow` and `@task` decorated functions (compatibility shim).
  - `task.submit()` dependency chains.
  - `task.map()` with moderate fan-out.
  - `@task(name=...)` custom task names (runtime + static forecast when tasks are module-level or flow-closure visible).
  - retries / timeouts / cancellation intent propagation.
  - concurrency limit tags (control-plane enforced).
  - **State transition hooks** (IronFlow extension, not Prefect API names): pass `transition_hooks=` to `@flow` / `@task` as a sequence of `TransitionHookSpec` from `on_transition(fn, from_state=..., to_state=...)`. `None` for `from_state` or `to_state` is a wildcard. Hooks run **synchronously in-process** after each successful control-plane transition (including the two edges produced by the batched `PENDING`/`RUNNING` start path), **without** holding the control-plane lock. User hook bodies may block arbitrarily; IronFlow only guarantees low overhead when **no** hooks are registered. Hook exceptions are logged and do not fail the run. Prefect’s separate `on_running` / `on_failure` / … style maps to explicit edges (e.g. `PENDING→RUNNING`, any `→FAILED`).
  - **Deployment schedules (subset):** interval schedules (`schedule_interval_seconds` + `schedule_next_run_at` + `schedule_enabled`), optional **cron** schedules (`schedule_cron`), and a Rust-preferred **RRule subset** (`schedule_rrule`). Schedule types are mutually exclusive in deployment state. The RRule subset supports `FREQ=MINUTELY|HOURLY|DAILY|WEEKLY`, optional positive `INTERVAL`, and optional `UNTIL`; `COUNT` and advanced calendar filters are intentionally unsupported for now. Comparisons use RFC3339 timestamps in UTC. When the native `rust-engine` library is loaded with `bind_db`, schedule ticks run in **Rust** (`deployment_ops`); the compat server prefers a **Rust background scheduler thread** and **Rust-backed blocking claim waits** when available. Python fallbacks cover interval and simple RRule schedules; cron schedules require the Rust path to compute ticks unless `schedule_next_run_at` is explicitly managed externally.
  - **CLI / YAML deploy (Tier 1 subset):** `ironflow init`, `ironflow deploy`, `ironflow serve`, and `ironflow worker start`; manifest file **`ironflow.yaml`** (`ironflow-version`, optional `pull`, `deployments[]` with `entrypoint` or `flow_name`, `parameters`, `work_pool.name`, `schedule`). Python **`prefect_compat.deploy.deploy()`** and **`serve()`** mirror CLI upsert + worker behavior. Tier 1 pull steps: `ironflow.deployments.steps.set_working_directory` only. Standalone workers share **`IRONFLOW_HISTORY_PATH`** with the API (`IRONFLOW_ENABLE_LOCAL_WORKER=0` on the server). Not Prefect `prefect deploy`, blocks, or full work-pool/recipe parity — see **`docs/how-to/deploy-with-cli.md`**.
- Not yet supported:
  - full API parity for every Prefect state rule edge case.
  - advanced cloud/tenant features.
  - all blocks and integrations.

## Phase 2 static planning compatibility

- Supported subset (planned):
  - analyzable `submit/map` dependency chains.
  - bounded loops with static upper bounds.
  - explicit task dependencies and resource hints.
- Fallback:
  - non-analyzable dynamic sections run via runtime path and are represented as opaque subgraphs.

## Notes

- This is an independent project, not an official Prefect release.
- Compatibility is workload-driven and expanded incrementally.
