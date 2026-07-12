---
name: ironflow-rust-hotpath
description: Decide when IronFlow logic belongs in rust-engine vs python-shim. Use when touching hot paths, state transitions, queries, schedules, serialization, or proposing a Python-only perf fix.
---

# IronFlow — Rust hot path

## Default

- **Rust (`rust-engine/`):** deterministic FSM, transition validation, append-only history, schedule ticks, claim waits, query/projection hot paths, FFI surface.
- **Python (`python-shim/`):** Prefect-like authoring, HTTP glue, thin wrappers over the native bridge, fallbacks when the `.so` is missing.

## When implementing

1. Prefer extending Rust + a thin Python binding over a Python-only loop on the control plane.
2. Keep shim endpoint/runtime logic orchestration-focused (no heavy scanning in request handlers).
3. Add Rust tests for transition/idempotency behavior; add shim tests for the public API.
4. If timing may change, run lite `perf_matrix` to `/tmp` (see `ironflow-perf-gate`).

## Do not

- Claim Prefect parity from a Rust-only internal change without `COMPATIBILITY.md` + tests.
- Silently change benchmark workload shapes while “optimizing.”
