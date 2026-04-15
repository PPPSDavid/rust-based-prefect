# Architecture Note (MVP)

## Runtime path

1. Python `@flow` / `@task` calls enter the compatibility shim.
2. Shim creates runs and proposes state transitions against a control-plane interface.
3. Rust engine owns deterministic transition validation and append-only event history.
4. Read-model projections expose timeline and run state for UI/API consumers.

## Static planning path

1. Source for selected Prefect-style patterns is parsed into Graph IR.
2. Unsupported dynamic constructs trigger diagnostics and dynamic fallback flags.
3. Forecast model emits task count, edge count, critical path, and parallelism estimate.

## Compatibility scope (MVP)

- `task.submit` chains
- `task.map` fan-out (subset)
- retries/timeouts/cancellation semantics at control-plane level
- concurrency-limit intent
