# Agent Working Guide

This repository is designed for iterative agent-assisted development. **AGENTS.md** (sometimes mirrored or abbreviated as **AGENT.md** in other repos) is the shared contract for *how* coding agents work here: coordination rules, ownership boundaries, validation commands, and pointers to ground-truth docs. Treat it as the operating manual for parallel, safe changes—not a substitute for `README.md`, `COMPATIBILITY.md`, or code.

All agents should **read the nearest `AGENTS.md` or `AGENT.md` before editing files in a directory tree** (walk upward from your target path until you find one). Repo-root `AGENTS.md` is authoritative for this project unless a deeper file explicitly overrides a narrower scope. If instructions conflict, prefer the more specific file for the area you are changing, then reconcile with maintainers if still unclear.

## Mission

Deliver a Prefect-compatible orchestration prototype that improves:

- state transition correctness and determinism
- scale/performance under high run and task volume
- pre-run graph visibility and forecasting for analyzable flow definitions

## Multi-Agent Rules

- **One worktree or workspace per agent; one branch per task.** Do not share a working directory with another active agent. Use separate clones or `git worktree` as needed; use normal Git fetch/rebase/merge to integrate work.
- **Start every task on a fresh feature branch** (never commit directly to `main` or reuse another agent’s branch for unrelated work). Parallel agents pushing to the same branch causes **lost commits, confusing history, and merge races**—treat separate branches as mandatory isolation, not optional hygiene.
- **Narrow, explicit scope.** Each task must name the files, modules, or layers in scope. Do not silently expand into areas owned by other in-flight tasks.
- **Prefer sequential hand-offs** (spec → implementation → tests → documentation) over many agents editing the same files concurrently.
- **Cross-cutting ideas** (wide refactors, API reshapes) should be filed as follow-up tasks or separate branches—not bundled into unrelated work.

## Tasks and Branches

Every task description should include: **goal**, **ownership area** (paths or modules), **forbidden areas** (what not to touch), and **acceptance criteria** (tests, behavior, or artifacts).

**Branch naming** (use lowercase `area` and kebab-case descriptions):

| Kind | Pattern | Example |
| --- | --- | --- |
| Feature | `feat/<area>-<short-description>` | `feat/python-shim-task-retries` |
| Bugfix | `fix/<area>-<short-description>` | `fix/rust-engine-state-idempotency` |
| Refactor | `refactor/<area>-<short-description>` | `refactor/static-planner-graph-cache` |
| Tests only | `test/<area>-<short-description>` | `test/benchmarks-perf-matrix-cli` |

Summarize work in the **branch description, commit messages, or PR body**: impacted modules/packages, user-visible behavior, and any migrations, config, or benchmark baseline updates.

### Cloud / Cursor agents (branch naming)

**Cursor Cloud** and similar hosted agents should follow the same **one branch per task** rule. Unless the task says otherwise, create a dedicated branch off `main` at the **start** of the run, using the project’s cloud convention when given one (for example `cursor/<short-description>-f251`). Do not pile unrelated changes onto an existing `cursor/*` branch that another session may still be using—**always** branch first to avoid cross-agent races on push, PR updates, and CI.

## Project Map & Ownership

| Area | What it is | Typical owner / focus |
| --- | --- | --- |
| `rust-engine/` | Deterministic orchestration kernel, FFI surface, hot paths | **Engine agent**: state machine, transitions, persistence hooks, performance-sensitive Rust |
| `python-shim/` | Prefect-compatible runtime, API, decorators, runners | **Shim/API agent**: compatibility surface, HTTP/runtime behavior, Python↔Rust glue |
| `static-planner/` | Static flow analysis and forecasting | **Planner agent**: graph extraction, forecasts, analyzer-only changes |
| `benchmarks/` | Performance harnesses, `perf_matrix.py`, Prefect vs IronFlow comparisons | **Benchmarks agent**: recipes, metrics, comparison tooling (coordinate with engine/shim if semantics change) |
| `frontend/` | Web UI (e.g. Vite/React) for runs/flows | **Frontend agent**: UI, client types, API usage—coordinate API contract changes with `python-shim` |
| `scripts/` | Server launchers, demos, stress/seed scripts | **Integration/scripts agent**: wiring and E2E-oriented scripts |
| `docs/` | Methodology, baselines, memory bank, perf artifacts | **Docs agent** or whoever changes behavior: keep `COMPATIBILITY.md` and benchmarks docs in sync |
| `tools/` | Dev tooling (e.g. MCP helpers, local scripts) | **Tooling agent**: isolated from runtime semantics unless agreed |
| `.github/` | CI workflows | **Infra/CI agent** (see Quality & Safety—often “ask first”) |

**Multi-repo / larger workspace:** IronFlow is self-contained in this repository. If your workspace includes other repos (e.g. shared libraries or deployment configs), treat them as separate ownership boundaries: **land dependency or contract changes first** (shared lib → engine/shim → API → frontend), and avoid spanning repos in a single agent branch unless the task explicitly includes integration.

## Hotspot Files (Single-Writer Rule)

These files tend to merge-conflict or silently affect the whole system. **At most one agent should actively edit a given hotspot at a time**; others treat them as **read-only** and open a follow-up task or branch instead of interleaving edits.

**Examples in this repo:** root `pytest.ini` (Python path layout); `rust-engine/Cargo.toml` / `rust-engine/Cargo.lock`; `python-shim/pyproject.toml` and `static-planner/pyproject.toml`; `frontend/package.json` and frontend lockfiles; `python-shim/src/prefect_compat/__init__.py` (public exports); `rust-engine/src/lib.rs` (crate exports); central route or API entry modules (e.g. `python-shim/src/prefect_compat/server.py`); `scripts/ironflow_server.py` (process wiring); `.github/workflows/*` (CI definitions).

Prefer **adding new modules or extension points** (new submodule, new route file included from a thin registry) over repeatedly editing the same central registry when parallel work is likely.

## Quality & Safety Requirements

### Always

- Run the **documented test commands** in **Expected Validation** (and any area-specific tests) before committing or opening a PR.
- Keep edits within the **declared task scope** and ownership areas.
- **Update or add tests** when behavior changes; update docs when user-visible or compatibility behavior changes.

### Ask first (or use a separate task)

- Database schema changes, long-running migrations, or destructive data operations.
- CI/CD pipeline changes, infra, secrets management, or permission scopes.
- Introducing **new major dependencies** or materially changing benchmark methodology.

### Never

- Commit secrets, tokens, API keys, or credentials.
- Edit **generated artifacts**, vendored third-party trees, or **lockfiles** unless the task explicitly requires those edits.
- **Force-push** to shared or protected branches or change default-branch protection outside maintainer process.

## Workflow Examples

1. **Feature touching API + UI:** Shim agent implements HTTP/types in `python-shim/` on `feat/python-shim-flow-cancel`; frontend agent consumes the contract on `feat/frontend-flow-cancel`. Merge **shim first**, then frontend (or frontend branch rebases after API lands). Tests: shim tests + frontend tests; optional E2E via `scripts/`.
2. **Engine refactor + test hardening:** Agent A lands `refactor/rust-engine-state-helpers` with behavior-neutral moves; Agent B follows on `test/rust-engine-edge-cases` adding tests only—no simultaneous edits to the same Rust modules without hand-off.
3. **CI + application change:** Agent A proposes workflow updates on `feat/infra-perf-gate` (reviewed/approved); Agent B implements app changes on `feat/benchmarks-threshold` only after the CI contract is clear, or B uses feature flags / non-breaking defaults until A merges.

## Evolving This File

When ownership areas shift, new hotspots appear, or validation commands change, **update this document in the same PR** as the structural change when possible. Prefer small, reviewable edits; announce cross-cutting process changes in PR descriptions so other agents can re-read the relevant section.

## Ground Truth Files

- `README.md` for project entry points
- `RELEASING.md` / `VERSION` for releases and synchronized versions across Rust/Python/frontend
- `COMPATIBILITY.md` for supported Prefect semantics
- `docs/MEMORY_BANK.md` for session handoff context
- `docs/perf_methodology.md` for workload dimensions and **`perf_matrix.py` metrics/thresholds**
- `docs/perf_comparison.json` for **Prefect vs IronFlow** A/B numbers (`benchmarks/compare_prefect_vs_ironflow.py`) — **not** a `perf_matrix compare` input
- `docs/perf_matrix_results.json` / `docs/perf_matrix_summary.md` for **`perf_matrix.py run` outputs** (when present)
- Section **Performance: perf_matrix.py** (below) for the canonical agent runbook (`run` / `compare`, modes, exit codes)

## Development Conventions

- Keep compatibility decisions explicit in `COMPATIBILITY.md`.
- Prefer additive, test-backed changes over broad rewrites.
- If behavior changes, update tests and docs in same change.
- Preserve benchmark reproducibility (do not silently change workload shapes).
- Treat Python as a bridge layer; move all performance-critical hot paths to Rust.

## Expected Validation

Run before declaring completion:

1. `python -m pytest python-shim/tests static-planner/tests benchmarks/tests` (from repo root; `pytest.ini` adds `python-shim/src`, `static-planner/src`, and `.` to `PYTHONPATH`)
2. `cargo test --manifest-path rust-engine/Cargo.toml`
3. After any significant change/refactor/new feature, run a deterministic perf check to guard against regressions:
   - Fast local gate: `python benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2`
   - If a baseline exists: `python benchmarks/perf_matrix.py compare --baseline <baseline.json> --candidate <candidate.json>` — only valid when both runs share the same **benchmark mode** (`metadata.matrix_compare_key`, e.g. `preset:lite` vs `preset:full`). If modes differ, the tool skips comparison and exits `3` (exit `2` = metric regression, `1` = bad input file such as a non–perf-matrix JSON).

## Performance: `perf_matrix.py` (read this before using or changing benchmarks)

This is the **deterministic control-plane matrix** harness. It is **not** the same tool as `benchmarks/compare_prefect_vs_ironflow.py` (which writes `docs/perf_comparison.json`).

### Commands

- **Run:** `python benchmarks/perf_matrix.py run --preset <lite|pr|full|hook_micro> [...]`
  - Optional: `--recipes a,b,c` overrides the preset; the recipe set defines the **benchmark mode** fingerprint.
  - Defaults write `docs/perf_matrix_results.json` + `docs/perf_matrix_summary.md` (override with `--out-json` / `--out-md`).
- **Compare:** `python benchmarks/perf_matrix.py compare --baseline <path> --candidate <path>`
  - **Inputs must be JSON objects** from a previous `run` (they include `aggregates` and `metadata`).
  - **Do not** pass `docs/perf_comparison.json` or any other JSON **array** — the loader rejects them with a clear error.

### Benchmark modes (comparable only within the same mode)

Each run stores `metadata.matrix_compare_key`, e.g. `preset:lite` when the recipe list matches that preset, or `recipes:...` for a custom set. **`compare` runs metrics only when baseline and candidate keys match.** If you captured a baseline with `--preset lite` and later run `--preset full`, comparison is **skipped** (not a false regression): exit **`3`**, with an explanation that you need a baseline for that mode.

### Exit codes (`compare`)

| Code | Meaning |
| ---: | --- |
| `0` | Compared; no threshold regression |
| `2` | Compared; at least one metric regressed |
| `3` | **Skipped** — baseline/candidate benchmark mode mismatch |
| `1` | Input error (e.g. wrong JSON type or missing file) |

### Further reading

- `docs/perf_methodology.md` — workload dimensions, metrics, thresholds, CI notes.

## MCP and Tooling Guidance

- Prefer `gh` CLI for GitHub workflows (issues/PRs/releases).
- Use browser automation MCP only when UI validation is required.
- Keep benchmark output artifacts in `docs/` and avoid checking in noisy transient logs.

## Decision Defaults for Future Agents

- Prioritize correctness and deterministic behavior over feature breadth.
- Prefer subset parity with strong tests over incomplete full parity claims.
- If adding new compatibility surface, add at least one script-level E2E test.
- For any query-heavy, serialization-heavy, or state-transition hot path:
  - implement in `rust-engine/`
  - expose to Python via thin FFI/binding wrappers
  - keep Python endpoint/runtime logic lightweight and orchestration-focused

## Review Process

For significant code review and key technical decisions, all agents must use this decision protocol before finalizing recommendations:

```
Simulate three different experts answering the question below.
Each expert will write down one step of their thinking and share it with the group.
Then all experts move to the next step.
If any expert realizes they're wrong at any point, they drop out.
Continue until there’s consensus on the final answer.
```

Apply this method to architecture trade-offs, compatibility choices, performance-sensitive changes, and non-trivial refactors. Minor edits (typos, comments, straightforward bug fixes) can use normal review flow.


<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
