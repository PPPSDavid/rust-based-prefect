# Compatibility Matrix

This document tracks compatibility targets against Prefect OSS.

## Baseline

- Upstream project: `prefecthq/prefect` (self-hosted OSS context)
- Baseline major/minor: `3.x`
- Initial validation target: `3.0.0`

## Phase 1 runtime compatibility (current MVP target)

- Supported:
  - `@flow` and `@task` decorated functions (compatibility shim).
  - `task.submit()` dependency chains.
  - `task.map()` with moderate fan-out.
  - retries / timeouts / cancellation intent propagation.
  - concurrency limit tags (control-plane enforced).
  - **State transition hooks** (IronFlow extension, not Prefect API names): pass `transition_hooks=` to `@flow` / `@task` as a sequence of `TransitionHookSpec` from `on_transition(fn, from_state=..., to_state=...)`. `None` for `from_state` or `to_state` is a wildcard. Hooks run **synchronously in-process** after each successful control-plane transition (including the two edges produced by the batched `PENDING`/`RUNNING` start path), **without** holding the control-plane lock. User hook bodies may block arbitrarily; IronFlow only guarantees low overhead when **no** hooks are registered. Hook exceptions are logged and do not fail the run. Prefect’s separate `on_running` / `on_failure` / … style maps to explicit edges (e.g. `PENDING→RUNNING`, any `→FAILED`).
  - **Deployment schedules (subset):** interval schedules (`schedule_interval_seconds` + `schedule_next_run_at` + `schedule_enabled`) and optional **cron** schedules (`schedule_cron`, mutually exclusive with a positive interval). Comparisons use RFC3339 timestamps in UTC. When the native `rust-engine` library is loaded with `bind_db`, deployment row writes and schedule ticks run in **Rust** (`deployment_ops`); the compat server prefers a **Rust background scheduler thread** and **Rust-backed blocking claim waits** when available, with Python fallbacks if the library is missing or outdated.
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
