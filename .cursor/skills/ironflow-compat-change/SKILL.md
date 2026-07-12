---
name: ironflow-compat-change
description: Add or change Prefect-compatibility behavior in IronFlow. Use when editing COMPATIBILITY.md, prefect_compat APIs, state semantics, or claiming parity with Prefect.
---

# IronFlow — compatibility change

1. Read `COMPATIBILITY.md` and `docs/compatibility_review_workflow.md`.
2. Classify the gap: supported / partial / missing / out of scope.
3. Prefer Rust-owned hot paths; keep Python as a thin bridge.
4. Implement the smallest testable subset; add shim and/or planner tests.
5. Update `COMPATIBILITY.md` in the same PR; do not claim full Prefect parity.
6. Validate: `python3 -m pytest python-shim/tests static-planner/tests` (+ Rust tests if engine touched).
7. If control-plane timing may change, run lite `perf_matrix` gate from root `AGENTS.md`.
