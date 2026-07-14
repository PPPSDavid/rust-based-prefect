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

## Compatibility scope (MVP)

- `task.submit` chains
- `task.map` fan-out (subset)
- `@task(name=...)` and repeated same-task invocations in one flow
- retries/timeouts/cancellation semantics at control-plane level
- deployment concurrency limits (per-deployment run caps)
- global + tag concurrency limits (Rust slot ledger; see `docs/how-to/concurrency-limits.md`)
