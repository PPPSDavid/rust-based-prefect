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

Canonical sorted backlog (gap vs deliberate vs park): **`docs/plans/prefect-gap-canvas.md`** (2026-07 canvas).

These are not commitments; shortlist retained for quick scanning (updated July 2026).

| Candidate | Current Prefect surface | IronFlow status | Why it fits |
| --- | --- | --- | --- |
| Docs sitemap / matrix honesty | Prefect `llms.txt` + concept IA | **Partial** — nav/`llms.txt` drift; matrix under-lists open gaps (P0 in canvas) | Cheap; unblocks every later session |
| Task resume / result store | Retry + caching overlap | **In flight** — PR #50 / `docs/plans/task-result-cache.md`; `main` still re-runs all tasks | Determinism + cancel/retry UX |
| Runtime DX (context, logging, lifecycle) | Logger/context; **force cancel + pause drain/terminate + resume** | Design: `docs/plans/flow-run-lifecycle-control.md` (**P3.2**); today cancel is state-only | Core after P0–P1; before P2 |
| UI ops experience & aesthetics | Operate hundreds of flows; modern shell | Surfaces exist; polish lagging — **PU** track `docs/plans/ui-ops-experience.md` (render→confirm) | Parallel priority; not Prefect clone |
| Custom transition hooks (X→Y→Z) | Side effects on state edges at definition time | **Core shipped** (`transition_hooks` / `on_transition` on `@flow`/`@task`); **PH** = docs/sugar/workers | High leverage polish, not greenfield |
| Global + tag concurrency polish | Async CM, CLI `gcl`, UI admin, perf gate | Core sync subset shipped; ops surface open (**P4** deep dive) | Productize existing Rust ledger |
| Postgres Rust schedule/gate | Self-hosted scale | Claim/lease on Postgres shipped; schedule/gate may Python-fallback | After P3/P4 core DX |
| RRule deployment schedules | Cron, Interval, RRule | Limited Rust-first RRule; advanced rules missing | Only if a real workload needs it |
| `task.delay()` background tasks | Worker-backed delay | Missing | Design before queue semantics |
| Variables JSON store | Structured JSON variables | No API | Small; lower priority than resume/logging |
| Events/automations subset | Events + automations OSS | Events/SSE only; no automation engine | Design-first (P7 in canvas) |

