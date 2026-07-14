# Memory Bank

Compact context handoff for future sessions. Process/validation contract: root `AGENTS.md`.
Last updated: 2026-07-12.

## Project Snapshot

- Name: Project IronFlow (`rust-based-prefect`)
- Goal: Prefect-compatible orchestration with stronger determinism, performance, and static planning.
- Status: Hybrid MVP in active use — deployments (schedules, CLI/YAML Tier 1); **subflows M1+M2** on `main` (#34/#36) with user guide at `docs/how-to/subflows.md`; transition hooks; agent tooling under `.cursor/` + `docs/agent/`. **Active plan:** self-hosted Docker + basic auth — `docs/plans/self-hosted-docker-auth.md` (not started).

## Core Architecture

- `rust-engine/`: deterministic state-machine kernel and append-only event model.
- `python-shim/`: Prefect-style ergonomics (`@flow`, `@task`, `submit`, `map`, `wait_for`, **`deployment_ref` / subflows**) with compatibility runtime + optional FastAPI server.
- `static-planner/`: static graph IR + forecast for supported flow subset (`@flow` body, `submit`/`map`, repeated tasks, `@task(name=...)`, UI DAG logical/expanded).
- `benchmarks/`: `perf_matrix.py` (control-plane matrix) and `compare_prefect_vs_ironflow.py` (A/B vs Prefect).

## Compatibility Baseline

- Prefect target: `3.x` (subset-first). Source of truth: `COMPATIBILITY.md`.
- Review loop before matrix changes: `docs/compatibility_review_workflow.md`.
- Do not claim full Prefect parity without matrix + tests.

## Persistence Status

- Dual local persistence in shim runtime:
  - JSONL append history for durable event replay
  - SQLite read model for query/API/UI reads
- Query / schedule / claim hot paths prefer Rust when the native bridge is loaded.

## Agent tooling (code-review-graph)

- Upstream: https://github.com/tirth8205/code-review-graph
- Cloud/Linux default: `.cursor/environment.json` → `bash .cursor/cloud-install.sh` (includes CRG setup).
- Manual: `bash scripts/setup_code_review_graph.sh` (core package, no embeddings) + `python3 scripts/verify_code_review_graph.py`.
- MCP: `.cursor/mcp.json` → `python3 tools/dev/crg_mcp_serve.py` (tool ids use `*_tool` suffix).
- Graph DB: `.code-review-graph/` (gitignored). Details: `tools/dev/README.md`.

## Performance Artifacts

- Methodology: `docs/perf_methodology.md`
- Control-plane matrix: `python3 benchmarks/perf_matrix.py run --preset lite …` → `docs/perf_matrix_results.json`
- Prefect A/B (different tool): `benchmarks/compare_prefect_vs_ironflow.py` → `docs/perf_comparison.json`
- Do **not** pass `perf_comparison.json` to `perf_matrix.py compare`.

## Useful Commands

- Python tests: `python3 -m pytest python-shim/tests static-planner/tests benchmarks/tests`
- Rust tests: `cargo test --manifest-path rust-engine/Cargo.toml`
- Perf gate: `python3 benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2`
- CRG setup/verify: `bash scripts/setup_code_review_graph.sh`

## Run lifecycle: cancel / retry

**Current behavior:**

- **Cancel** (`POST /api/flow-runs/{id}/cancel`): sets flow run state to `CANCELLED` and marks in-flight task runs `CANCELLED` in the control plane / SQLite read model. Long-running task bodies are not cooperatively interrupted unless they poll cancellation themselves (no default hook yet).
- **Retry** (`POST /api/flow-runs/{id}/retry`): for deployment-backed runs, triggers a **new** deployment run → **new** flow run with **`resume_from_flow_run_id`**. Eligible completed tasks may skip (see below); this is **not** full Prefect task-resume / `cache_policy` parity.

**Task resume (Phase 1 — landed):**

- Design: **`docs/plans/task-result-cache.md`**. User guide: **`docs/how-to/task-resume-and-persist.md`**.
- Skip on resume when prior return was **`None`** (auto) or **`@task(persist_result=True)`** stored a JSON-safe payload. Non-persisted non-`None` recomputes. UI shows persisted results on Task Runs / Artifacts.
- Follow-ups: map-index hardening, parameter-guard, Rust hot-path lookup, subflow/gate policies.

**Useful test scenario (manual / E2E):**

- Flow: fast task → `sleep` ~10s task → downstream task. Trigger → cancel while sleeping → retry → wait for completion. With `persist_result` / `None` markers, expect eligible tasks to skip on retry. UI visual: `scripts/seed_persist_result_ui.py` + `frontend/e2e/persist-result-ui.spec.ts`.

## Next High-Value Work

1. Move remaining projection write hot paths from Python into Rust-backed implementation.
2. Expand Prefect API compatibility matrix with concrete parity tests.
3. Add migration/versioning path toward PostgreSQL for larger-scale persistence.
4. Keep CI + `perf_matrix` regression thresholds healthy.
5. **Task-level resume on flow-run retry** — Phase 1 landed. Follow-ups: map-index resume hardening, parameter-guard, Rust hot-path lookup, subflow/gate policies.
6. Optional: Cloud embeddings path if NL `semantic_search` becomes important; keep decision log current (`docs/agent/DECISION_LOG.md`).
