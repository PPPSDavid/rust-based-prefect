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
  - `task.submit()` dependency chains; with **`ThreadPoolTaskRunner`** (default), independent submits return immediately and run concurrently (bodies in a shared thread pool). **`SequentialTaskRunner`** keeps submit non-overlapping. **`ProcessPoolTaskRunner`**: `submit()` remains synchronous; use `map()` for process concurrency.
  - `task.map()` with moderate fan-out.
  - `@task(name=...)` custom task names (runtime + static forecast when tasks are module-level or flow-closure visible).
  - retries / timeouts / cancellation intent propagation.
  - **Deployment concurrency (subset):** per-deployment `concurrency_limit` with collision strategies `ENQUEUE` / `CANCEL_NEW`, enforced on the deployment-run claim / trigger path (Rust-preferred when `bind_db` is active). This caps concurrent **deployment runs** for one deployment — not the same as named global/tag slots below.
  - **Global concurrency limits (subset):** named slot ledger in SQLite (Rust-preferred); CRUD via control plane + HTTP `/api/concurrency-limits`; sync `concurrency(...)` context manager (`occupy`, `strict`, `timeout_seconds`, leases) and `rate_limit(...)` when `slot_decay_per_second` is set. Soft-missing defaults match Prefect (warn + proceed unless `strict=True`). See **`docs/how-to/concurrency-limits.md`**.
  - **Tag-based concurrency limits (subset):** `@task(tags=...)`; limits named `tag:{tag}` (via `create_tag_concurrency_limit` or GCL CRUD); AND across tags on enter `Running`; limit `0` aborts (`CANCELLED` + error). Tag wait poll: `IRONFLOW_TASK_TAG_SLOT_WAIT_SECONDS`. Thread/process `map` acquires per worker so fan-out respects the cap.
  - **State transition hooks** (IronFlow extension, not Prefect API names): pass `transition_hooks=` to `@flow` / `@task` as a sequence of `TransitionHookSpec` from `on_transition(fn, from_state=..., to_state=...)`. `None` for `from_state` or `to_state` is a wildcard. Hooks run **synchronously in-process** after each successful control-plane transition (including the two edges produced by the batched `PENDING`/`RUNNING` start path), **without** holding the control-plane lock. User hook bodies may block arbitrarily; IronFlow only guarantees low overhead when **no** hooks are registered. Hook exceptions are logged and do not fail the run. Prefect’s separate `on_running` / `on_failure` / … style maps to explicit edges (e.g. `PENDING→RUNNING`, any `→FAILED`).
  - **Deployment schedules (subset):** interval schedules (`schedule_interval_seconds` + `schedule_next_run_at` + `schedule_enabled`), optional **cron** schedules (`schedule_cron`), and a Rust-preferred **RRule subset** (`schedule_rrule`). Schedule types are mutually exclusive in deployment state. The RRule subset supports `FREQ=MINUTELY|HOURLY|DAILY|WEEKLY`, optional positive `INTERVAL`, and optional `UNTIL`; `COUNT` and advanced calendar filters are intentionally unsupported for now. Comparisons use RFC3339 timestamps in UTC. When the native `rust-engine` library is loaded with `bind_db`, schedule ticks run in **Rust** (`deployment_ops`); the compat server prefers a **Rust background scheduler thread** and **Rust-backed blocking claim waits** when available. Python fallbacks cover interval and simple RRule schedules; cron schedules require the Rust path to compute ticks unless `schedule_next_run_at` is explicitly managed externally.
  - **CLI / YAML deploy (Tier 1 subset):** `ironflow init`, `ironflow deploy`, `ironflow serve`, and `ironflow worker start`; manifest file **`ironflow.yaml`** (`ironflow-version`, optional `pull`, `deployments[]` with `entrypoint` or `flow_name`, `parameters`, `work_pool.name`, `schedule`). Python **`prefect_compat.deploy.deploy()`** and **`serve()`** mirror CLI upsert + worker behavior. Tier 1 pull steps: `ironflow.deployments.steps.set_working_directory` only. Standalone workers share **`IRONFLOW_HISTORY_PATH`** with the API (`IRONFLOW_ENABLE_LOCAL_WORKER=0` on the server). Not Prefect `prefect deploy`, blocks, or full work-pool/recipe parity — see **`docs/how-to/deploy-with-cli.md`**.
  - **Self-hosted basic auth (subset):** optional `IRONFLOW_SERVER_API_AUTH_STRING` / `IRONFLOW_API_AUTH_STRING` HTTP Basic auth on `/api/*` (Prefect OSS-shaped; no RBAC). See **`docs/how-to/secure-self-hosted.md`**.
  - **Server Docker image (Tier A):** `deploy/docker/Dockerfile.server` — single-container API with embedded worker/scheduler; see **`docs/how-to/docker-quickstart.md`** and **`deploy/docker/README.md`** (PyPI wheel + uvicorn runtime).
  - **Persistence backends (Tier B1 subset):** default **SQLite** sidecar (file/`IRONFLOW_HISTORY_PATH`); optional **Postgres** via `IRONFLOW_DATABASE_URL`. Claim/lease hot paths bind in **Rust** for both backends; schedule ticks and most CRUD fall back to Python on Postgres until follow-ups. See **`docs/how-to/database-postgres.md`** and **`docs/plans/self-hosted-storage-rfc.md`**.
  - **HTTP workers (Tier B2 subset):** `IRONFLOW_WORKER_MODE=http` + `POST /api/workers/claim` / `…/runs/{id}/started|finished` so workers never open the control-plane DB (Prefect-shaped multi-host). Default remains `file` (shared history path). See **`docs/how-to/worker-http-mode.md`**.
  - **Docker Compose + services (Tier B3/B5 subset, shipped):** `deploy/docker/compose.yml` — Postgres + API (`ENABLE_SCHEDULER/LOCAL_WORKER=0`) + `ironflow server services start` + HTTP worker; GHA `docker-compose-smoke.yml`. HA multi-services leader election and Redis deferred. See **`docs/how-to/docker-compose.md`** (Prefect compose guide is the structural reference).
  - **Subflows (subset):** two mechanisms only — (1) **blocking inline** via direct `child_flow(...)` from an active parent `@flow` (same process; linked child flow run with `execution_mode=inline`, parent/root/depth metadata); (2) **deployment-backed subflow as task** via `deployment_ref(name_or_id).submit(**params)` returning `SubflowFuture` (surrogate parent task `kind=subflow`, child deployment run + child flow run linkage). `wait_for=[subflow_future]` and `wait([...])` gate downstream tasks; **fire-and-forget** requires `submit(..., detach=True)` (or `@flow(final_state="explicit")`) so the child is excluded from parent final-state aggregation. Nesting of either mechanism inside either mechanism is supported (depth capped, currently 32). Parent cancel propagates to **active** deployment-backed children. UI: DAG node kinds `inline_subflow` / `subflow_task`; parent run detail exposes `children[]` and child-run navigation. User guide: **`docs/how-to/subflows.md`**.
  - **Flow-run final state (IronFlow extension):** default `@flow(final_state="wait_all")` waits for all non-detached task / gate / subflow children, then resolves the flow terminal state in **Rust** (`resolve_flow_terminal_state`: `CANCELLED` > `FAILED` > all `COMPLETED`). Unobserved failed concurrent `submit`s fail the flow (`FlowChildrenFailed`). Escape hatches: `submit(..., detach=True)` and `@flow(final_state="explicit")` (body return/exception remains authoritative). Not Prefect return-value / `State`-object finalization.
  - **Temporal gate tasks (IronFlow extension):** `gate(name=..., max_wait=...)` inside an active `@flow`; call `.submit(until=datetime | after=timedelta, wait_for=[...])` to insert a **zero-op barrier** task (`kind=gate`, real task-run UUID) that blocks downstream `wait_for` until `open_at`. Default **`max_wait`** safeguard is **`timedelta(days=1)`** (Python definitional default; override per gate). While waiting, the flow run may enter **`PAUSED`**; gate promotion ticks prefer **Rust** (`task_tick_gate_tasks` / bundled in `deployment_maintenance`) with Python fallback. UI: DAG node kind **`gate_task`** with `gate_open_at`. Not Prefect API parity — Prefect has no first-class in-flow calendar gate.
- Not yet supported (honest open gaps — not parity claims):
  - full API parity for every Prefect state rule edge case.
  - advanced cloud/tenant features (RBAC, SSO, workspaces) — **deliberate park** for MVP.
  - all blocks, secrets managers, and integration packs — **deliberate park**.
  - Prefect `SubflowTask` / `run_deployment` name parity, automatic deployment creation from `@flow`, or subflow parameter schema validation beyond deployment defaults.
  - **Task-level resume on flow-run retry** / opt-in result cache (retry today re-executes completed tasks; design/impl tracked in PR [#50](https://github.com/PPPSDavid/rust-based-prefect/pull/50) / `docs/plans/task-result-cache.md`).
  - Prefect-shaped logging helpers (`get_run_logger`, `log_prints`); log list/API/UI exist without those authoring helpers.
  - Cooperative cancel of long-running task bodies (control-plane cancel is recorded; bodies must poll unless instrumented — see `docs/MEMORY_BANK.md`).
  - User-facing artifacts API (`create_markdown` / tables); internal result artifact rows + GET APIs only.
  - Variables JSON store; settings/profiles module; runtime-context module parity.
  - Events → automations / webhooks engine (events + SSE exist; no trigger actions).
  - `task.delay()` background tasks; Dask/Ray runners; non-process work pool types — **park** near-term.
  - Async `concurrency` / `rate_limit` helpers, CLI `ironflow gcl` parity, UI concurrency admin page, work-queue / work-pool concurrency.
  - Advanced RRule calendar filters / `COUNT`; human-in-the-loop pause/input (temporal `gate` is a different, deliberate mechanism).

## Phase 2 static planning compatibility

- Supported subset (current):
  - `@flow` function body analysis for `submit` / `map` and `wait_for` dependencies.
  - `@task(name=...)` custom names when task objects are module-level or flow-closure visible.
  - Repeated invocations of the same task in one flow (`task-0`, `task-1`, … labels; distinct `planned_node_id` per call).
  - Distinct task wrappers on a shared Python function body (separate graph nodes per wrapper).
  - Bounded loops with static upper bounds (`for i in range(N)` where `N` is a constant).
  - Direct nested `@flow` calls and `deployment_ref(...).submit()` recognized for forecast/DAG node kinds where statically visible.
  - Per-run manifest + forecast (task/edge counts, critical path, parallelism).
  - Run DAG API and UI: **Aggregated fan-out** (`mode=logical`) / **Task runs** (`mode=expanded`); layout: dependencies left→right, parallel top→bottom; zoom-pan, search, path highlight; subflow node kinds `inline_subflow` / `subflow_task` (see `docs/concepts/dag-and-forecast.md`).
- Fallback:
  - Non-analyzable dynamic sections (`if`, `range(n)` with runtime `n`, tasks not visible to the compiler) run via the runtime path; DAG may show `source: runtime` with runtime-inferred nodes.

## Notes

- This is an independent project, not an official Prefect release.
- Compatibility is workload-driven and expanded incrementally.
