# Architecture Note (MVP)

IronFlow is intentionally **Rust-first**: the **`rust-engine`** crate is the **authoritative orchestration kernel** — deterministic state transitions, validation, and append-only history. **Python** (`prefect_compat`) is the **authoring and integration layer**: Prefect-like decorators, process orchestration glue, HTTP when enabled, and calls into the engine over FFI when the native library is loaded. The frontend (if used) observes state through the same persistence and APIs; it is not a second control plane.

## Runtime path

1. Python `@flow` / `@task` calls enter the compatibility shim.
2. The shim creates runs and **proposes** state transitions to the control plane; **the Rust engine applies and records** them (deterministic validation, append-only event history).
3. Read models and query paths are served from the projected store; heavy or correctness-critical query logic is implemented in **Rust** when the native bridge is active, with Python fallbacks where provided.
4. UI/API consumers read timelines and run state from that stack (SQLite / projections as implemented).

## Static planning path

1. On flow start, IronFlow compiles the **`@flow` function body** (AST) into graph IR via `static-planner/`.
2. Supported patterns: `submit` / `map` chains, `wait_for`, constant bounded loops, repeated same-task calls, and `@task(name=...)` when task objects are visible to the flow module/closure.
3. The manifest is stored per flow run; each `submit` receives the next matching **`planned_node_id`** (or a dynamic `dyn_<task>_<n>` id when the forecast is exhausted). **`map()`** shares one planned node across all fan-out task runs in a batch.
4. Unsupported constructs (`if`, dynamic `range(n)`, opaque control flow) set **`fallback_required`**; the UI/API can still build a runtime-inferred DAG from task-run history.
5. Forecast emits task count, edge count, critical path length, and parallelism estimate.

See **[DAG and forecast](concepts/dag-and-forecast.md)** for UI behavior and testing wide/long graphs.

## UI DAG view

The optional Vite/React frontend renders run DAGs from `GET /api/flow-runs/{id}/dag` with **Aggregated fan-out** (planned manifest, `mode=logical`) and **Task runs** (`mode=expanded`) views. Dependencies always flow **left → right**; parallel siblings stack **top → bottom**. GPU-accelerated zoom/pan, search-to-focus, and upstream/downstream path highlighting.

## Native wheel vs Python fallback

A **production** install uses the native `rust-engine` `cdylib` (PyPI wheel or a local `cargo build` plus `IRONFLOW_RUST_LIB`). When that library loads, the shim prefers Rust for FSM validation, queries, deployment claim/lease, GCL slots, and sqlite-backed schedule ticks.

Missing native library is a **degraded mode** for in-process authoring: Python fallbacks keep `@flow` / `@task` working so tests and demos can run without a wheel. Do not treat fallbacks as the production control plane.

## Module map (0.3.0)

### Python (`prefect_compat`)

| Module | Role |
| --- | --- |
| `runtime.py` | Thin facade: `InMemoryControlPlane` composes mixins. Public import path is unchanged. |
| `control_plane/` | Mixins: `types`, `rust_dispatch`, `runs`, `run_events`, `queries`, `dag`, `deployments`, `deployment_runs`, `gcl`, `gates`, `lifecycle`, `resume`, `store`, `base`. |
| `server.py` | FastAPI app, CORS, optional embedded worker/scheduler threads. |
| `routes/` | HTTP routers (`health`, `flow_runs`, `catalog`, `deployments`, `work_pools`, `concurrency`, `streams`, `workers`) plus `schemas`. |
| `flow_registry.py` | Demo `@flow` / `@task` plus `FLOW_REGISTRY` so workers/CLI do not import FastAPI. |
| `plane.py` | Process-wide control-plane singleton. |
| `decorators.py` | `@flow` / `@task` authoring (largest remaining Python file). |

`from prefect_compat.runtime import InMemoryControlPlane` remains the compatibility alias.

### Rust (`rust-engine`)

| Module | Role |
| --- | --- |
| `engine.rs` | FSM truth (`validate_transition`) and append-only history. |
| `ffi/` | Unchanged C ABI (`ironflow_*`); dispatch split into `control_fsm`, `control_deployment`, `control_gcl`, `control_terminal`. |
| `deployment_ops/` | Claim, CRUD, schedule, tick, lifecycle, rows. |
| `concurrency_ops/` | Global/tag concurrency acquire and release. |
| Domain ops | `gate_ops`, `ui_read` / `ui_write`, `flow_terminal_ops`, `deployment_ops_pg`. |

### Persistence and schema ownership

- **SQLite** is the zero-config default; **Postgres** via `IRONFLOW_DATABASE_URL`; **JSONL** history for replay.
- Table DDL lives in the Python store adapters. Rust `ensure_schema` covers GCL and `bind_db` extras. Do not add a fourth schema owner. An Alembic-style migrator is a follow-up, not this release.

## Compatibility scope (MVP)

- `task.submit` chains
- `task.map` fan-out (subset)
- `@task(name=...)` and repeated same-task invocations in one flow
- retries/timeouts/cancellation semantics at control-plane level
- deployment concurrency limits (per-deployment run caps)
- global + tag concurrency limits (Rust slot ledger; see `docs/how-to/concurrency-limits.md`)
