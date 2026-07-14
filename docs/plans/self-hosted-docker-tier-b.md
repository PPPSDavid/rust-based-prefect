# Tier B — Production Docker / Compose Implementation Plan

**Status:** Ready to implement (Tier A + C landed on `main` via [#46](https://github.com/PPPSDavid/rust-based-prefect/pull/46))  
**Last updated:** 2026-07-14  
**Depends on:** `docs/plans/self-hosted-docker-auth.md` (parent tracking)  
**Parent refs:** Prefect [server-docker](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-docker), [docker-compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose), [server-cli](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli)

This is the **executable** plan for full production-shaped self-hosting. Tier A (single-container server) and Tier C (basic auth) already ship. Tier B is the path to Prefect-compose parity: **Postgres + HTTP workers + services split + compose**.

(`docs/plans/**` is not published to MkDocs.)

---

## 0. Baseline after A+C (verified 2026-07-14)

| Item | State |
| --- | --- |
| Version | `0.2.0` on `main` |
| Server image | `deploy/docker/Dockerfile.server` + `scripts/docker_server_smoke.sh` + GHA workflow |
| Auth | `IRONFLOW_SERVER_API_AUTH_STRING` / `IRONFLOW_API_AUTH_STRING` |
| Worker claim protocol | **Direct SQLite** (`claim_next_deployment_run` via `bind_db` / Python) |
| Persistence | JSONL + SQLite sidecar (`IRONFLOW_HISTORY_PATH`) |
| Rust DB | `rusqlite` only — `bind_db` takes a **file path** |
| Docker Compose | Not present |
| Tables (SQLite) | `flow_runs`, `task_runs`, `dag_manifests`, `logs`, `events`, `artifacts`, `deployments`, `deployment_runs`, `workers`, `work_pools` (+ upgrades) |
| Rust deployment ops | `deployment_create/update/claim_next/trigger_run/…/maintenance`, `task_tick_gate_tasks` |

### Agent / Cloud test reality (this machine)

| Capability | Available? | Implication for Tier B |
| --- | --- | --- |
| System **PostgreSQL 16** | **Yes** (verified: connect + `ironflow_schema_migrations` probe) | B1/B2 integration tests can run **without Docker** via `IRONFLOW_DATABASE_URL` |
| **Docker / compose** daemon | **No** on Cloud agent VM | Full compose E2E (`B5`) must run in **GitHub Actions** (or a Docker-capable host) |
| Related unit tests | 22 passed (`test_deployments_runtime`, `test_worker_module`, `test_api_auth`) | Good regression baseline before storage refactors |

**Rule:** Prefer **Postgres-backed pytest + Testcontainers-or-service Postgres in CI** for control-plane correctness. Reserve compose smoke for B5 packaging.

---

## 1. Locked product / architecture decisions

Decide these in **B0**; treat as non-negotiable unless RFC revises them:

1. **Two modes forever (at least through B5):**  
   - **Dev / single-node:** SQLite (+ Rust `bind_db`) — zero config.  
   - **Production compose:** Postgres via `IRONFLOW_DATABASE_URL` — network DB.
2. **Workers in production are HTTP clients** (Prefect model). No shared volume.  
3. **JSONL:** remain **optional audit/replay** in both modes (append-only). Postgres/SQLite is the query/control plane of record for compose. Do not dual-write SQLite+Postgres.  
4. **Rust + Postgres:** **defer** native Postgres in `rust-engine` past B1 MVP. Production compose uses **Python control-plane paths** (already exist as fallbacks when `bind_db` absent / not used). Local SQLite keeps Rust hot paths. Document perf difference honestly.  
5. **B-fast (shared volume)** = optional / discouraged; only if a demo needs split containers before B2.  
6. **UI auth prompt (C3)** still open — fold into B5 if compose ships UI.

---

## 2. Delivery slices (PRs)

One PR per slice when hotspots conflict; otherwise small pairs OK.

| Slice | Branch hint | Goal | Approx. risk |
| --- | --- | --- | --- |
| **B0** | `feat/python-shim-control-plane-store` | RFC + `ControlPlaneStore` protocol; SQLite adapter = current behavior | Med (refactor) |
| **B1a** | `feat/python-shim-postgres-store` | Alembic/SQL migrations + Postgres adapter + `IRONFLOW_DATABASE_URL` | High |
| **B1b** | `feat/python-shim-database-cli` | `ironflow server database upgrade/reset` + optional migrate-from-files | Med |
| **B2** | `feat/python-shim-http-worker` | Claim/finish HTTP API + `IRONFLOW_WORKER_MODE=http` | High |
| **B3** | `feat/python-shim-server-services` | Services process + advisory lock | Med |
| **B5a** | `feat/deploy-docker-compose` | Dockerfiles worker/services/ui + `compose.yml` | Med |
| **B5b** | `feat/ci-docker-compose-smoke` | GHA compose E2E + auth overlay | Med |
| **B4** | *(later)* | Redis / multi-worker API | Low priority |

**Do not** ship B5 compose claiming “production” until B1a + B2 land.

---

## 3. Development plan by slice

### B0 — RFC + storage abstraction

**Code**

- Add `python-shim/src/prefect_compat/persistence/`  
  - `protocol.py` — typing `Protocol` for claim, deployments, runs, events, workers, schedules.  
  - `sqlite_store.py` — extract SQLite SQL currently in `InMemoryControlPlane` (behavior-neutral move).  
  - `factory.py` — select store from env (`IRONFLOW_DATABASE_URL` unset → SQLite).
- Keep `InMemoryControlPlane` as façade (name historical); methods delegate to store.
- **Hotspots:** `runtime.py`, `server.py` — single-writer; land B0 alone if contested.

**RFC (`docs/plans/self-hosted-storage-rfc.md`)**

- Mode matrix; JSONL fate; Rust Postgres deferral rationale; failure modes (lease reclaim, NFS).  
- Explicit non-goals: Cloud RBAC, Prefect blocks.

**Tests**

- Existing deployment/worker suites must stay green **unchanged**.  
- Optional: assert factory returns SqliteStore for default env.

**Validation**

```bash
uv run pytest python-shim/tests/test_deployments_runtime.py \
  python-shim/tests/test_worker_module.py \
  python-shim/tests/test_deploy_rust_path.py
cargo test --manifest-path rust-engine/Cargo.toml
```

---

### B1a — Postgres store + migrations

**Code**

- Dependency: `psycopg[binary]` (optional extra or server extra — prefer **`ironflow-prefect-compat[server]`** so CLI-only installs stay thin).  
- Schema: port the 10 tables (+ indexes/partial unique on idempotency) to Postgres; replace `AUTOINCREMENT` with `BIGSERIAL` / sequences; use `TIMESTAMPTZ` or keep TEXT ISO-8601 for first cut (TEXT ISO reduces dialect drift — **prefer TEXT ISO for MVP**.
- Migrations: Alembic **or** ordered SQL files + `ironflow_schema_migrations` table (probe already works locally).  
- Env: `IRONFLOW_DATABASE_URL`, `IRONFLOW_DATABASE_MIGRATE_ON_START` (containers default `true`).  
- When URL set: **do not** call Rust `bind_db`; force Python deployment ops.

**Tests (comprehensive; this environment can run them)**

| Test | Purpose |
| --- | --- |
| `test_postgres_schema_upgrade` | Fresh DB migrates clean |
| `test_postgres_create_claim_finish` | Deployment run lifecycle |
| `test_postgres_idempotency_key` | Partial unique index behavior |
| `test_postgres_concurrency_limit` | ENQUEUE / CANCEL_NEW |
| `test_postgres_schedule_tick` | Interval tick enqueue |
| `test_sqlite_parity_subset` | Same scenarios against SQLite store for parity |

**Local (Cloud agent / laptop without Docker):**

```bash
# Postgres 16 already proven on this Cloud image:
#   postgresql://ironflow:ironflow@127.0.0.1:5432/ironflow
export IRONFLOW_DATABASE_URL=postgresql://ironflow:ironflow@127.0.0.1:5432/ironflow
uv run pytest python-shim/tests/test_postgres_*.py -q
```

**CI:** `services: postgres:16` job `postgres-store` on Ubuntu (no Docker-in-Docker needed).

**Perf gate:** After B1a, run lite `perf_matrix` on SQLite path only (Postgres path is correctness-first; optional separate recipe later).

---

### B1b — Database CLI + file→Postgres migrator

**Code**

- `ironflow server database upgrade|reset`  
- `ironflow server database migrate-from-files --history-path …` (one-shot import)

**Tests**

- CLI `--help` / dry-run  
- Migrator round-trip: seed SQLite via existing tests → export → Postgres → assert counts

**Docs:** `docs/how-to/database-postgres.md`

---

### B2 — HTTP worker protocol

**API (additive)**

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/workers/heartbeat` | Exists — keep; require auth when set |
| `POST` | `/api/workers/claim` | Body: `name`, `work_pool_id`, `lease_seconds`; optional long-poll `wait_ms` |
| `POST` | `/api/workers/runs/{id}/started` | Mark RUNNING |
| `POST` | `/api/workers/runs/{id}/finished` | Status COMPLETED/FAILED/CANCELLED + error |
| `GET` | `/api/workers/runs/{id}` | Optional debug |

**Claim response must include:** `deployment_run` fields + deployment `entrypoint` / `flow_name` / `resolved_parameters` so workers need **no DB**.

**Worker**

- `IRONFLOW_WORKER_MODE=http|file` (containers default `http`)  
- Reuse `execute_claimed_deployment_run` after HTTP claim  
- Heartbeat via HTTP  
- Auth: send `IRONFLOW_API_AUTH_STRING`

**Tests**

| Test | Purpose |
| --- | --- |
| `test_http_claim_exclusive` | Two workers → one claim |
| `test_http_lease_reclaim` | Expired lease reclaimable |
| `test_http_worker_loop_executes_entrypoint` | End-to-end with thread + TestClient/ASGI |
| `test_http_auth_required` | 401 without creds when server auth set |
| Multi-process subprocess worker | Optional integration |

**Local without Docker:** FastAPI + Postgres + in-process/httpx worker — **fully runnable here**.

**Parity scripts:** extend `scripts/docker_server_smoke.sh` or add `scripts/http_worker_smoke.sh` (API + worker on host against PG).

---

### B3 — Background services split

**Code**

- CLI: `ironflow server services start` — scheduler/maintenance loop only  
- Env already: `IRONFLOW_ENABLE_SCHEDULER`  
- Leader election: Postgres `pg_try_advisory_lock` (or lease row) so two services replicas don’t double-tick  
- Compose: `services` container with health via heartbeat file or tiny `/healthz` on alternate port (optional)

**Tests**

- Advisory lock unit test  
- Two threads/processes; assert single ticker advances `schedule_next_run_at`

---

### B5 — Images + compose + CI

**Images** (wheel-based like server)

| Image | CMD |
| --- | --- |
| `ironflow-server` | uvicorn; `ENABLE_LOCAL_WORKER=0`, `ENABLE_SCHEDULER=0`, migrate-on-start |
| `ironflow-services` | `ironflow server services start` |
| `ironflow-worker` | `ironflow worker start --mode http` |
| `ironflow-ui` | nginx + static Vite (`VITE_API_BASE`) |

**Compose services:** `postgres`, `server`, `services`, `worker`, optional `ui`  
**Overlay:** `compose.auth.yml`

**Smoke:** `scripts/docker_compose_smoke.sh`

1. `compose up -d --wait`  
2. `ironflow database upgrade` (or migrate-on-start)  
3. `ironflow deploy` + trigger  
4. Poll deployment-run / flow-run terminal state  
5. Tear down  

**Machine note:** this Cloud agent **cannot** run B5 smoke. CI **must**.

---

### B4 — Redis (defer)

Only when API horizontal scale is required. Keep B5 compose Redis **commented optional**.

---

## 4. Test pyramid (what “comprehensive” means)

```
                 ┌─────────────────────────────┐
                 │  B5 compose E2E (GHA only)  │  ← packaging + network
                 └──────────────▲──────────────┘
                                │
                 ┌──────────────┴──────────────┐
                 │ B2 HTTP worker + Postgres   │  ← host: pytest + PG (+ optional subprocess)
                 │ B3 leader election          │
                 └──────────────▲──────────────┘
                                │
                 ┌──────────────┴──────────────┐
                 │ B1 Postgres store suite     │  ← host PG / CI service
                 │ SQLite↔Postgres parity      │
                 └──────────────▲──────────────┘
                                │
                 ┌──────────────┴──────────────┐
                 │ Existing unit/integration   │  ← always green
                 │ (deployments, workers, auth)│
                 └─────────────────────────────┘
```

### Mandatory gates before merge of each slice

| Slice | Must pass |
| --- | --- |
| B0 | Existing deploy/worker/rust tests |
| B1a | New postgres suite + existing tests + `ruff`/`ty` |
| B1b | CLI + migrator tests |
| B2 | HTTP worker suite (+ auth matrix) |
| B3 | Lock / dual-process test |
| B5 | **New** GHA `docker-compose-smoke` green |

### Perf

- Do **not** change `perf_matrix` workload shapes.  
- After B0/B1 refactor of SQLite path: lite preset guard.  
- Postgres path: optional future recipe — out of scope until B5 stable.

---

## 5. Release & CI changes

### CI workflows (concrete)

| Workflow | Change |
| --- | --- |
| `.github/workflows/ci.yml` | Add job `postgres-store` with `services.postgres:16`, runs `test_postgres_*.py` when paths touch persistence |
| `.github/workflows/docker-server-smoke.yml` | Keep Tier A; expand path filters when worker Dockerfile lands |
| **New** `.github/workflows/docker-compose-smoke.yml` | B5: build images (`INSTALL_MODE=local`), `compose up`, run smoke script; path filters on `deploy/docker/**`, worker/server code |
| Docs workflow | Already strict — keep nav/docs in sync (how-to matrix §6) |

### Image publish (extend `RELEASING.md`)

1. Publish **`ironflow-prefect-compat==VERSION`** to PyPI (existing).  
2. Build/push (GHCR):  
   - `ghcr.io/pppsdavid/ironflow-server:VERSION`  
   - `ghcr.io/pppsdavid/ironflow-worker:VERSION`  
   - `ghcr.io/pppsdavid/ironflow-services:VERSION`  
   - `ghcr.io/pppsdavid/ironflow-ui:VERSION`  
3. Tags: `VERSION`, minor float optional, avoid relying on `latest` in docs.  
4. New **`workflow_dispatch`** `publish-ghcr.yml` (after B5a) — PyPI-first then GHCR, same as Tier A guidance.  
5. GitHub Release notes: compose one-liner + `docker pull` list.

### Package extras (recommended)

```toml
[project.optional-dependencies]
server = ["fastapi>=0.115,<1", "uvicorn[standard]>=0.30,<1", "psycopg[binary]>=3.1"]
```

Images install `ironflow-prefect-compat[server]==VERSION`.

### Semver notes

- Additive HTTP routes + Postgres opt-in: **minor** (0.3.0) once B2 usable.  
- Removing file-worker mode: **major** (document in B2.7; do not remove in B5).

---

## 6. Documentation deliverables (per slice)

| Doc | Slice |
| --- | --- |
| `docs/plans/self-hosted-storage-rfc.md` | B0 |
| `docs/how-to/database-postgres.md` | B1 |
| `docs/how-to/worker-http-mode.md` | B2 |
| `docs/how-to/run-background-services.md` | B3 |
| `docs/how-to/docker-compose.md` | B5 |
| Updates: `SELF_HOSTED_SERVER.md`, `env-vars.md`, `api.md`, `COMPATIBILITY.md`, `PREFECT_IRONFLOW_MAPPING.md`, `RELEASING.md`, `mkdocs.yml` | each |

---

## 7. Forbidden / caution areas

- Do not silently change `perf_matrix` recipes.  
- Do not force-push `main`.  
- Avoid dual SQLite+Postgres writers.  
- Hotspots: `runtime.py`, `server.py`, `Cargo.toml` — sequence writers.  
- Cloud agents: do not claim compose verified without GHA evidence.

---

## 8. Suggested calendar of work (agent-sized, not wall-clock)

Technical depth, not time estimates:

1. **B0** — large mechanical extract; low user visibility; unblocks everything.  
2. **B1a** — largest correctness risk; needs thorough PG suite (local PG available).  
3. **B2** — product breakthrough (multi-host workers).  
4. **B3** — small once DB exists.  
5. **B5 + CI** — packaging; blocked on GHA Docker for full E2E.

**Skip B-fast** unless a stakeholder needs a demoware split tomorrow.

---

## 9. Acceptance checklist (Tier B done)

- [ ] B0 RFC merged; SQLite store behind protocol  
- [ ] Postgres mode: migrate, CRUD, claim, schedule on CI `services.postgres`  
- [ ] HTTP worker mode default in compose; file mode still works for SQLite demos  
- [ ] Services container with single-leader scheduler  
- [ ] `deploy/docker/compose.yml` (server + services + worker + postgres + optional ui)  
- [ ] GHA compose smoke green  
- [ ] Docs + COMPATIBILITY rows; no false Prefect Full parity claims  
- [ ] RELEASING updated for multi-image GHCR publish  

---

## 10. Appendix — current inventory (for B1 mapping)

**SQLite tables:** `flow_runs`, `task_runs`, `dag_manifests`, `logs`, `events`, `artifacts`, `deployments`, `deployment_runs`, `workers`, `work_pools`.

**Rust FFI ops requiring `bind_db` today:**  
`deployment_create`, `deployment_update`, `deployment_claim_next`, `deployment_claim_next_wait`, `deployment_trigger_run`, `deployment_get_run`, `deployment_cancel_by_parent_flow`, `deployment_reclaim_expired`, `deployment_worker_heartbeat`, `deployment_tick_schedules`, `deployment_reap_stale_workers`, `deployment_mark_run_started`, `deployment_attach_flow_run`, `deployment_mark_run_finished`, `deployment_maintenance`, `task_tick_gate_tasks`.

**Local PG probe (2026-07-14):** `postgresql://ironflow:ironflow@127.0.0.1:5432/ironflow` reachable; `ironflow_schema_migrations` creatable.

**Related tests green (pre-B):** deployments runtime + worker module + auth = 22 passed.
