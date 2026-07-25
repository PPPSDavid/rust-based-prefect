# Prefect Compatibility Review Workflow

This maintainer note defines the repeatable loop for keeping IronFlow's compatibility story aligned with Prefect while preserving IronFlow's Rust-first, focused-scope design.

Use this workflow before implementing a new Prefect-alignment feature, and whenever `COMPATIBILITY.md` feels stale.

## Inputs

- Local source of truth: `COMPATIBILITY.md`
- Prefect-facing map: `docs/PREFECT_IRONFLOW_MAPPING.md`
- Architecture constraints: `docs/architecture.md`, `AGENTS.md`
- Current upstream references:
  - Prefect concepts: https://docs.prefect.io/v3/concepts
  - What's new in Prefect 3: https://docs.prefect.io/v3/get-started/whats-new-prefect-3
  - Prefect release notes: https://github.com/PrefectHQ/prefect/releases

Prefer official Prefect docs and release notes. If a feature has changed recently or the docs disagree with memory, trust upstream docs and cite the page used in the review summary.

## Pass Structure

1. **Read our current matrix.**
   Start with `COMPATIBILITY.md` and `docs/PREFECT_IRONFLOW_MAPPING.md`. List the features we claim as supported, partial, planned, or unsupported.

2. **Scan Prefect's current surface.**
   Check Prefect's concepts and recent release notes for material changes in workflows, tasks, states, deployments, schedules, workers, concurrency, caching, artifacts, variables, events, and automations.

3. **Build a gap table.**
   For each relevant Prefect feature, classify IronFlow as:
   - `supported`: works in the documented subset and has tests.
   - `partial`: implemented with narrower semantics than Prefect.
   - `missing`: useful alignment candidate, no meaningful support yet.
   - `out of scope`: intentionally excluded for this project phase.
   - `unknown`: needs code/test inspection before claiming.

4. **Apply IronFlow filters.**
   Prefer gaps that fit the project philosophy:
   - deterministic state transitions and idempotent control-plane behavior.
   - Rust-owned hot paths, scheduling, queueing, persistence, and validation.
   - thin Python compatibility APIs over broad Python-only emulation.
   - small, testable subsets over vague full-parity claims.

5. **Propose before implementing.**
   Present two or three candidate gaps with:
   - what Prefect supports.
   - what IronFlow already supports.
   - the smallest useful subset to add.
   - likely files/modules touched.
   - acceptance tests.
   - docs updates.

6. **Update docs with the selected scope.**
   Before or alongside implementation, update `COMPATIBILITY.md` to name the exact supported subset and explicit non-goals. If the feature changes user-facing usage, update the relevant docs page too.

7. **Implement narrowly.**
   Create a task branch and keep edits inside the chosen ownership area. For runtime/control-plane work, put correctness-sensitive logic in `rust-engine/` first when practical, then expose it through `python-shim/`.

8. **Validate.**
   Run the relevant focused tests, then the expected validation commands from `AGENTS.md` when the change is significant:
   - `python -m pytest python-shim/tests static-planner/tests benchmarks/tests`
   - `cargo test --manifest-path rust-engine/Cargo.toml`
   - `python benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2` for significant runtime changes.

## Review Output Template

```markdown
## Prefect Compatibility Pass: <date>

Sources checked:
- <official Prefect docs/release links>
- <local docs/files checked>

Current IronFlow support:
- Supported: ...
- Partial: ...
- Missing: ...
- Out of scope: ...

Recommended candidates:
1. <feature>
   - Prefect behavior:
   - IronFlow status:
   - Proposed subset:
   - Rust-first design:
   - Tests/docs:
2. <feature>
3. <feature>

Recommendation:
<one feature and why>
```

## Current Candidate Backlog

These are not commitments; they are starting points from the May 2026 pass (updated July 2026 for concurrency).

| Candidate | Current Prefect surface | IronFlow status | Why it fits |
| --- | --- | --- | --- |
| Global + tag concurrency limits | Named slot ledger, `concurrency` / `rate_limit`, leases, decay; tags as `tag:{name}` on enter `Running`. | **Implemented** subset — how-to `docs/how-to/concurrency-limits.md`; plan retained for follow-ups (async CM, CLI, UI). | Core control-plane feature; Rust lease/acquire fits existing claim patterns. |
| RRule deployment schedules | Prefect supports Cron, Interval, and RRule schedules. | Limited Rust-first RRule subset implemented; advanced calendar rules remain missing. | Bounded scheduling feature that fits existing Rust deployment scheduler paths. |
| Minimal task caching | Prefect task caching uses cache keys, policies, expiration, storage, and isolation. | Phase 1 subset: DAG resume + `persist_result` JSON allowlist (`docs/plans/task-result-cache.md`). | Good determinism/idempotency story; keep serialization narrow. |
| `task.delay()` background tasks | Prefect supports fire-and-forget background task execution via workers. | `.submit()` and `.map()` exist; `.delay()` is missing. | Aligns with existing deployment queue/worker concepts, but needs careful future and queue semantics. |
| Variables JSON store | Prefect variables support structured JSON configuration. | No documented variable API. | Small API/storage feature, but lower value than control-plane alignment. |
| Events/automations subset | Prefect 3 OSS includes events and automations. | IronFlow records events and has transition hooks, but no automation engine. | Strong conceptual fit, but broader product surface; design before implementation. |

