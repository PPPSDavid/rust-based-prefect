# Memory Bank

Compact context handoff for future sessions. Process/validation contract: root `AGENTS.md`.
Last updated: 2026-09-04.

## Project Snapshot

- Name: Project IronFlow (`rust-based-prefect`)
- Goal: Prefect-compatible orchestration with stronger determinism, performance, and static planning.
- Status: **0.3.0 maintainer-cleanup series** on top of post-`v0.2.0` product that already shipped: deployments (schedules, CLI/YAML Tier 1); **subflows M1+M2** (#34/#36) with `docs/how-to/subflows.md`; transition hooks; **global + tag concurrency limits** (`docs/how-to/concurrency-limits.md`, CLI `ironflow gcl`, UI Concurrency page); concurrent `task.submit` via ThreadPoolTaskRunner; **flow-run final state `wait_all`**; **task resume / persist_result**; cancel / drain|terminate pause; agent tooling under `.cursor/` + `docs/agent/`. **Self-hosted (core shipped):** Tier A server Docker + Tier C basic auth + Tier B0–B3/B5 (#49/#52/#56/#57) — Postgres, HTTP workers, `ironflow server services start`, `deploy/docker/compose.yml`, GHA compose smoke. Guides: `docs/how-to/docker-compose.md`, `docs/SELF_HOSTED_SERVER.md`. **Follow-ups (not blocking):** HA services leader election, Alembic-style DB upgrade CLI, Redis/multi-worker API (B4), UI compose image, GHCR publish automation — see `docs/plans/self-hosted-docker-tier-b.md`.

## 0.3.0 structure (this series)

- Quality gates: rustfmt + clippy `-D warnings`, `ruff format`, ruff `C901` max 20, clippy `too_many_lines` 120, `scripts/code_metrics.py` file-LOC ratchet (new files ≤800; existing must not cross 1000 unless allowlisted; allowlisted files must not grow).
- Python control plane: `runtime.py` is a mixin facade; logic lives in `prefect_compat/control_plane/`. FastAPI lives in `server.py` + `routes/`; demo flows in `flow_registry.py`.
- Rust: C ABI `ironflow_*` unchanged; dispatch in `ffi/control_*.rs`; `deployment_ops/` and `concurrency_ops/` are packages. No `rust-engine/src` file >800.
- Allowlisted production file >1000 LOC: **`decorators.py` only** (parked: `benchmarks/perf_matrix.py`).
- Hosted docs: Get started ≤4 nav entries; published pages must not MkDocs-link into excluded `docs/plans/**`.
- **Python wheels:** GIL **3.11–3.14**; `scripts/check_python_support_matrix.py` ratchets CI to `requires-python`. Dev/Docker stay on **3.12**. Experimental **3.14t** is TestPyPI/CI-artifact only (`uv sync` skips `psycopg-binary`, which has no `cp314t` wheel).

## Core Architecture

- `rust-engine/`: deterministic state-machine kernel (`engine.rs` `validate_transition`) and append-only event model. FFI stays ctypes C ABI (no PyO3).
- `python-shim/`: Prefect-style ergonomics (`@flow`, `@task`, `submit`, `map`, `wait_for`, **`deployment_ref` / subflows**) with compatibility runtime + optional FastAPI extra `server`. **`ThreadPoolTaskRunner`**: concurrent `submit` + `map`. **`ProcessPoolTaskRunner`**: registered child processes per task (cancel/terminate SIGTERM→SIGKILL).
- `static-planner/`: static graph IR + forecast for supported flow subset (`@flow` body, `submit`/`map`, repeated tasks, `@task(name=...)`, UI DAG logical/expanded).
- `benchmarks/`: `perf_matrix.py` (control-plane matrix) and `compare_prefect_vs_ironflow.py` (A/B vs Prefect). Do not split `perf_matrix.py` in 0.3.0.
- Native wheel = production; Python fallbacks = degraded in-process authoring.

## Compatibility Baseline

- Prefect target: `3.x` (subset-first). Source of truth: `COMPATIBILITY.md`.
- Review loop before matrix changes: `docs/compatibility_review_workflow.md`.
- Do not claim full Prefect parity without matrix + tests. 0.3.0 does **not** expand COMPATIBILITY claims.

## Persistence Status

- Dual local persistence in shim runtime (dev / single-node default):
  - JSONL append history for durable event replay
  - SQLite read model for query/API/UI reads via `prefect_compat.persistence` (`SqliteStore` / `ControlPlaneStore`; B0 extract)
- **Postgres** via `IRONFLOW_DATABASE_URL` (`PostgresStore`); Rust `bind_db` for claim/lease on both backends. Schedule ticks / most CRUD on Postgres may still fall back to Python until follow-ups.
- Query / schedule / claim / GCL hot paths prefer Rust when the native bridge is loaded.
- Production compose uses Postgres + HTTP workers (no shared worker filesystem).
- Schema owners: Python store DDL + Rust `ensure_schema` for GCL/`bind_db`. Do not add a fourth copy. Alembic is a follow-up.

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
- Historical candidate/compare dumps: `docs/archive/perf/` (not published).
- Do **not** pass `perf_comparison.json` to `perf_matrix.py compare`.

## Useful Commands

- Python tests: `python3 -m pytest python-shim/tests static-planner/tests benchmarks/tests`
- Rust tests: `cargo test --manifest-path rust-engine/Cargo.toml`
- Lint wrapper: `bash scripts/lint.sh` (ruff/ty/fmt/clippy + `scripts/code_metrics.py`)
- Perf gate: `python3 benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2`
- CRG setup/verify: `bash scripts/setup_code_review_graph.sh`

## Run lifecycle: cancel / pause / retry

**Current behavior:**

- **Cancel** (`POST /api/flow-runs/{id}/cancel`): `CANCELLED` + in-flight task rows cancelled; records `lifecycle_action=cancel`, `interrupt_mode=terminate`. Under **`ProcessPoolTaskRunner`**, registered children get SIGTERM→grace→SIGKILL (`process_workers.py`). Thread-pool bodies remain cooperative-only. User guide: **`docs/how-to/cancel-pause-resume.md`**.
- **Pause** (`POST …/pause` with required `mode=drain|terminate`): drain blocks new starts and settles `PAUSED`; terminate cancels RUNNING rows then kills process workers and holds `PAUSED`. Resume is operator-pause only (`POST …/resume`) — after terminate, in-process runs call `prepare_resume` (P1) and terminalize the prior attempt; deployment-backed use retry-with-`resume_from`. Plan: `docs/plans/flow-run-lifecycle-control.md`.
- **Retry** (`POST /api/flow-runs/{id}/retry`): for deployment-backed runs, triggers a **new** deployment run → **new** flow run with **`resume_from_flow_run_id`**. Eligible completed tasks may skip (see below).

**Task resume (Phase 1 — landed):**

- Design: **`docs/plans/task-result-cache.md`**. User guide: **`docs/how-to/task-resume-and-persist.md`**.
- Skip on resume when prior return was **`None`** (auto) or **`@task(persist_result=True)`** stored a JSON-safe payload, **and** flow/deployment params + submit/`map` input fingerprints match. `map` uses `map_index`. Cache hits do not re-fire transition hooks. Non-persisted non-`None` recomputes. UI shows persisted results plus **skipped** / **recomputed** labels on resume attempts.
- Follow-ups: native Rust `resume_from` on deployment ops (Python merge bridge today), subflow/gate policies; UI pause-mode chooser **shipped** (P3.2e).

**Useful test scenario (manual / E2E):**

- Flow: fast task → `sleep` ~10s task → downstream task. Trigger → cancel while sleeping → retry → wait for completion. With `persist_result` / `None` markers, expect eligible tasks to skip on retry. UI visual: `scripts/seed_persist_result_ui.py` + `frontend/e2e/persist-result-ui.spec.ts`.


## Next High-Value Work

Gap canvas: `docs/plans/prefect-gap-canvas.md` (from PR #60 lineage).

1. **P4.1** — async `concurrency` / `rate_limit` (thin over the same Rust acquire). See `docs/plans/north-stars-later.md`.
2. Postgres Rust schedule/gate + HA follow-ups (P2) — later plan.
3. Keep CI + `perf_matrix` lite gate healthy (including `--preset gcl`) **and** `pytest -m airtight`.
4. P1 resume follow-ups: native Rust `resume_from`, subflow/gate policies.
5. Move remaining projection write hot paths from Python into Rust-backed implementation.
6. Optional: Cloud embeddings path if NL `semantic_search` becomes important; keep decision log current (`docs/agent/DECISION_LOG.md`).
7. Cheap hosted e2e (GHCR pull-and-smoke) — later plan, not always-on cloud.
8. Split `decorators.py` / `perf_matrix.py` (parked from 0.3.0 hard-cap).
