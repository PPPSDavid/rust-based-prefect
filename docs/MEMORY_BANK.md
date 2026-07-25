# Memory Bank

Compact context handoff for future sessions. Process/validation contract: root `AGENTS.md`.
Last updated: 2026-07-25.

## Project Snapshot

- Name: Project FlowOxide (`rust-based-prefect` repo URL kept for continuity/SEO; product renamed from IronFlow)
- Goal: Prefect-compatible orchestration with stronger determinism, performance, and static planning.
- Branding: PyPI `flowoxide-prefect-compat`, CLI `flowoxide`, env `FLOWOXIDE_*`, Rust crate `flowoxide-engine`.
- Status: Hybrid MVP in active use — deployments (schedules, CLI/YAML Tier 1); **subflows M1+M2** on `main` (#34/#36) with user guide at `docs/how-to/subflows.md`; transition hooks; **global + tag concurrency limits** (`docs/how-to/concurrency-limits.md`); concurrent `task.submit` via ThreadPoolTaskRunner; **flow-run final state `wait_all`** (Rust `resolve_flow_terminal_state`, `detach` / `final_state="explicit"` escape); agent tooling under `.cursor/` + `docs/agent/`. **Self-hosted (core shipped):** Tier A server Docker + Tier C basic auth + Tier B0–B3/B5 (#49/#52/#56/#57) — Postgres, HTTP workers, `flowoxide server services start`, `deploy/docker/compose.yml`, GHA compose smoke. Guides: `docs/how-to/docker-compose.md`, `docs/SELF_HOSTED_SERVER.md`. **Follow-ups (not blocking):** HA services leader election, Alembic-style DB upgrade CLI, Redis/multi-worker API (B4), UI compose image, GHCR publish automation — see `docs/plans/self-hosted-docker-tier-b.md`. Flow-run final state and concurrency plans implemented.

## Core Architecture

- `rust-engine/`: deterministic state-machine kernel and append-only event model.
- `python-shim/`: Prefect-style ergonomics (`@flow`, `@task`, `submit`, `map`, `wait_for`, **`deployment_ref` / subflows**) with compatibility runtime + optional FastAPI server. **`ThreadPoolTaskRunner`**: concurrent `submit` + `map`; process-pool `submit` still sync.
- `static-planner/`: static graph IR + forecast for supported flow subset (`@flow` body, `submit`/`map`, repeated tasks, `@task(name=...)`, UI DAG logical/expanded).
- `benchmarks/`: `perf_matrix.py` (control-plane matrix) and `compare_prefect_vs_flowoxide.py` (A/B vs Prefect).

## Compatibility Baseline

- Prefect target: `3.x` (subset-first). Source of truth: `COMPATIBILITY.md`.
- Review loop before matrix changes: `docs/compatibility_review_workflow.md`.
- Do not claim full Prefect parity without matrix + tests.

## Persistence Status

- Dual local persistence in shim runtime (dev / single-node default):
  - JSONL append history for durable event replay
  - SQLite read model for query/API/UI reads via `prefect_compat.persistence` (`SqliteStore` / `ControlPlaneStore`; B0 extract)
- **Postgres** via `FLOWOXIDE_DATABASE_URL` (`PostgresStore`); Rust `bind_db` for claim/lease on both backends. Schedule ticks / most CRUD on Postgres may still fall back to Python until follow-ups.
- Query / schedule / claim / GCL hot paths prefer Rust when the native bridge is loaded.
- Production compose uses Postgres + HTTP workers (no shared worker filesystem).

## Agent tooling (code-review-graph)

- Upstream: https://github.com/tirth8205/code-review-graph
- Cloud/Linux default: `.cursor/environment.json` → `bash .cursor/cloud-install.sh` (includes CRG setup).
- Manual: `bash scripts/setup_code_review_graph.sh` (core package, no embeddings) + `python3 scripts/verify_code_review_graph.py`.
- MCP: `.cursor/mcp.json` → `python3 tools/dev/crg_mcp_serve.py` (tool ids use `*_tool` suffix).
- Graph DB: `.code-review-graph/` (gitignored). Details: `tools/dev/README.md`.

## Performance Artifacts

- Methodology: `docs/perf_methodology.md`
- Control-plane matrix: `python3 benchmarks/perf_matrix.py run --preset lite …` → `docs/perf_matrix_results.json`
- Prefect A/B (different tool): `benchmarks/compare_prefect_vs_flowoxide.py` → `docs/perf_comparison.json`
- Do **not** pass `perf_comparison.json` to `perf_matrix.py compare`.

## Useful Commands

- Python tests: `python3 -m pytest python-shim/tests static-planner/tests benchmarks/tests`
- Rust tests: `cargo test --manifest-path rust-engine/Cargo.toml`
- Perf gate: `python3 benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2`
- CRG setup/verify: `bash scripts/setup_code_review_graph.sh`

## Run lifecycle: cancel / retry (current vs desired)

**Current behavior (MVP — do not change without explicit task):**

- **Cancel** (`POST /api/flow-runs/{id}/cancel`): sets flow run state to `CANCELLED` and marks in-flight task runs `CANCELLED` in the control plane / SQLite read model. Long-running task bodies are not cooperatively interrupted unless they poll cancellation themselves (no default hook yet).
- **Retry** (`POST /api/flow-runs/{id}/retry`): for deployment-backed runs, calls `trigger_deployment_run` with the same deployment and parameters → **new deployment run → new flow run → full flow re-execution from scratch**. This is **not** Prefect task-resume parity.

**Known gap (documented, future work):**

- For multi-task flows where some tasks **completed** before cancel, **retry currently recomputes those completed tasks**. Desired Prefect-like semantics: on retry, **already-completed tasks should not be recomputed** (task-level resume / result cache keyed by flow run lineage or equivalent).
- Implementing this requires architectural work: task result persistence across retry, idempotent resume graph, and UI/API surfacing of which tasks were skipped vs re-run. Track in compatibility matrix before claiming parity.

**Useful test scenario (manual / E2E):**

- Flow: fast task → `sleep` ~10s task → downstream task. Trigger → cancel while sleeping → retry → wait for completion. Today, expect all tasks to run again on retry; use this to validate when resume lands.

## Next High-Value Work

**P0 docs truth (nav / `llms.txt` / matrix / UI checklist / port guide)** is the current docs-hygiene bar; gap-canvas backlog proposal lives in PR [#60](https://github.com/PPPSDavid/rust-based-prefect/pull/60) (`docs/plans/prefect-gap-canvas.md` when merged).

1. **P1 task resume on retry** — finish/land PR [#50](https://github.com/PPPSDavid/rust-based-prefect/pull/50) Goal A; then map-key / Rust / UI hardening (see section above).
2. **P3 logging helpers** (`get_run_logger` / `log_prints`) + cooperative cancel polling.
3. Postgres Rust schedule/gate (Tier B follow-up) + optional Alembic upgrade CLI / HA services.
4. Keep CI + `perf_matrix` regression thresholds healthy (including `--preset gcl`).
5. Optional: async `concurrency` / CLI `gcl` / UI admin for concurrency limits.
6. Move remaining projection write hot paths from Python into Rust-backed implementation.
7. Optional: Cloud embeddings path if NL `semantic_search` becomes important; keep decision log current (`docs/agent/DECISION_LOG.md`).
