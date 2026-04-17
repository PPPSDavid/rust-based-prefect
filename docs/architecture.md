# Architecture Note (MVP)

IronFlow is intentionally **Rust-first**: the **`rust-engine`** crate is the **authoritative orchestration kernel** — deterministic state transitions, validation, and append-only history. **Python** (`prefect_compat`) is the **authoring and integration layer**: Prefect-like decorators, process orchestration glue, HTTP when enabled, and calls into the engine over FFI when the native library is loaded. The frontend (if used) observes state through the same persistence and APIs; it is not a second control plane.

## Runtime path

1. Python `@flow` / `@task` calls enter the compatibility shim.
2. The shim creates runs and **proposes** state transitions to the control plane; **the Rust engine applies and records** them (deterministic validation, append-only event history).
3. Read models and query paths are served from the projected store; heavy or correctness-critical query logic is implemented in **Rust** when the native bridge is active, with Python fallbacks where provided.
4. UI/API consumers read timelines and run state from that stack (SQLite / projections as implemented).

## Static planning path

1. Source for selected Prefect-style patterns is parsed into Graph IR.
2. Unsupported dynamic constructs trigger diagnostics and dynamic fallback flags.
3. Forecast model emits task count, edge count, critical path, and parallelism estimate.

## Compatibility scope (MVP)

- `task.submit` chains
- `task.map` fan-out (subset)
- retries/timeouts/cancellation semantics at control-plane level
- concurrency-limit intent
