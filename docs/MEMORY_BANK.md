# Memory Bank

Compact context handoff for future sessions. Process/validation contract: root `AGENTS.md`.
Last updated: 2026-07-25.

## Project Snapshot

- Name: Project IronFlow (`rust-based-prefect`)
- Goal: Prefect-compatible orchestration with stronger determinism, performance, and static planning.
- Status: Hybrid MVP in active use — deployments (schedules, CLI/YAML Tier 1); **subflows M1+M2** on `main` (#34/#36) with user guide at `docs/how-to/subflows.md`; transition hooks; **global + tag concurrency limits** (`docs/how-to/concurrency-limits.md`); concurrent `task.submit` via ThreadPoolTaskRunner; **flow-run final state `wait_all`** (Rust `resolve_flow_terminal_state`, `detach` / `final_state="explicit"` escape); agent tooling under `.cursor/` + `docs/agent/`. **Self-hosted (core shipped):** Tier A server Docker + Tier C basic auth + Tier B0–B3/B5 (#49/#52/#56/#57) — Postgres, HTTP workers, `ironflow server services start`, `deploy/docker/compose.yml`, GHA compose smoke. Guides: `docs/how-to/docker-compose.md`, `docs/SELF_HOSTED_SERVER.md`. **Follow-ups (not blocking):** HA services leader election, Alembic-style DB upgrade CLI, Redis/multi-worker API (B4), UI compose image, GHCR publish automation — see `docs/plans/self-hosted-docker-tier-b.md`. Flow-run final state and concurrency plans implemented.

## Core Architecture

- `rust-engine/`: deterministic state-machine kernel and append-only event model.
- `python-shim/`: Prefect-style ergonomics (`@flow`, `@task`, `submit`, `map`, `wait_for`, **`deployment_ref` / subflows**) with compatibility runtime + optional FastAPI server. **`ThreadPoolTaskRunner`**: concurrent `submit` + `map`; process-pool `submit` still sync.
- `static-planner/`: static graph IR + forecast for supported flow subset (`@flow` body, `submit`/`map`, repeated tasks, `@task(name=...)`, UI DAG logical/expanded).
- `benchmarks/`: `perf_matrix.py` (control-plane matrix) and `compare_prefect_vs_ironflow.py` (A/B vs Prefect).

## Compatibility Baseline

- Prefect target: `3.x` (subset-first). Source of truth: `COMPATIBILITY.md`.
- Review loop before matrix changes: `docs/compatibility_review_workflow.md`.
- Do not claim full Prefect parity without matrix + tests.

## Persistence Status

- Dual local persistence in shim runtime (dev / single-node default):
  - JSONL append history for durable event replay
  - SQLite read model for query/API/UI reads via `prefect_compat.persistence` (`SqliteStore` / `ControlPlaneStore`; B0 extract)
- **Postgres** via `IRONFLOW_DATABASE_URL` (`PostgresStore`); Rust `bind_db` for claim/lease on both backends. Schedule ticks / most CRUD on Postgres may still fall back to Python until follow-ups.
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
- Prefect A/B (different tool): `benchmarks/compare_prefect_vs_ironflow.py` → `docs/perf_comparison.json`
- Do **not** pass `perf_comparison.json` to `perf_matrix.py compare`.

## Useful Commands

- Python tests: `python3 -m pytest python-shim/tests static-planner/tests benchmarks/tests`
- Rust tests: `cargo test --manifest-path rust-engine/Cargo.toml`
- Perf gate: `python3 benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2`
- CRG setup/verify: `bash scripts/setup_code_review_graph.sh`

## Run lifecycle: cancel / pause / retry

**Current behavior:**

- **Cancel** (`POST /api/flow-runs/{id}/cancel`): `CANCELLED` + in-flight task rows cancelled; records `lifecycle_action=cancel`, `interrupt_mode=terminate`. Thread-pool bodies are **not** OS-killed yet (P3.2c process registry still open).
- **Pause** (`POST …/pause` with required `mode=drain|terminate`): drain blocks new starts and settles `PAUSED`; terminate marks RUNNING tasks cancelled and holds `PAUSED`. Resume is operator-pause only (`POST …/resume`). Plan: `docs/plans/flow-run-lifecycle-control.md`.
- **Retry** (`POST /api/flow-runs/{id}/retry`): deployment-backed → new run / full re-execution today (task resume Goal A still in flight on PR #50/#62).

**Known gap:** hard terminate via process workers; UI pause chooser; P1 resume of interrupted tasks on hard-pause resume.

## Next High-Value Work

Gap canvas: `docs/plans/prefect-gap-canvas.md` (from PR #60 lineage).

1. **P1 task resume on retry** — land PR [#62](https://github.com/PPPSDavid/rust-based-prefect/pull/62) / [#50](https://github.com/PPPSDavid/rust-based-prefect/pull/50) Goal A.
2. **P3.2c–e** — process worker terminate + UI/CLI pause chooser; `log_prints=` optional.
3. **P4** concurrency ops (lease-on-cancel, CLI `gcl`, UI admin).
4. Postgres Rust schedule/gate + HA follow-ups (P2).
5. Keep CI + `perf_matrix` lite gate healthy.
6. Move remaining projection write hot paths from Python into Rust-backed implementation.
7. Optional: Cloud embeddings path if NL `semantic_search` becomes important; keep decision log current (`docs/agent/DECISION_LOG.md`).
