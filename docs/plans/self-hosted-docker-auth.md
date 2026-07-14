# Self-Hosted Docker & Access Control Plan (IronFlow)

**Status:** Tier A + C **done** (merged [#46](https://github.com/PPPSDavid/rust-based-prefect/pull/46)); Tier B tracked in **[`self-hosted-docker-tier-b.md`](self-hosted-docker-tier-b.md)**  
**Last updated:** 2026-07-14  
**Scope:** `deploy/docker/`, `python-shim/`, `rust-engine/`, `frontend/`, `scripts/`, `docs/`, `COMPATIBILITY.md`  
**User-facing docs (target):** see §13 Documentation matrix  
**Prefect references (baseline, not parity claims):**

- [Run a local Prefect server (CLI)](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli)
- [Run the Prefect server in Docker](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-docker)
- [Prefect Server via Docker Compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose)
- [Secure a self-hosted Prefect server](https://docs.prefect.io/v3/advanced/security-settings)

This document is the **tracking plan** for making IronFlow a credible **self-hosted** alternative: official container images, compose-based deployment, and Prefect-OSS-shaped security. (`docs/plans/**` is **not** published to the MkDocs site.)

---

## 1. Problem statement

IronFlow already supports a **split control plane / worker** model in code and docs (`docs/SELF_HOSTED_SERVER.md`, `ironflow worker start`, `IRONFLOW_ENABLE_LOCAL_WORKER=0`), but there is **no official container story** and **no API authentication**. Teams evaluating IronFlow as a faster, self-hosted Prefect alternative hit these blockers immediately:

| Gap | Impact |
| --- | --- |
| No Dockerfiles / published images | Cannot deploy without bespoke scripting |
| Server binds `127.0.0.1` by default | Broken inside containers without manual flags |
| UI is a separate Vite app with hardcoded API origin | Extra compose service + proxy config |
| Workers claim runs via **shared SQLite/JSONL**, not HTTP | Multi-host workers require shared filesystem (unlike Prefect compose) |
| No auth on `/api/*` | Unacceptable for any shared network |
| No Postgres / Redis path | Cannot scale API or match Prefect multi-worker mode |

**Goal:** Ship **Tier A** and **Tier C** quickly for a secured single-container dev path, then deliver **Tier B** as the full production-compose track — including **technical pre-steps** (network DB, HTTP workers, services split) before the Prefect-shaped `docker compose` stack.

---

## 2. Current architecture (baseline)

| Piece | Location | Notes |
| --- | --- | --- |
| API server | `python-shim/src/prefect_compat/server.py` | FastAPI; embedded scheduler + optional embedded worker on startup |
| Scheduler | Same process (thread or Rust background) | `deployment_maintenance_tick()` |
| Standalone worker | `ironflow worker start` / `ironflow serve` | Opens `InMemoryControlPlane` at `IRONFLOW_HISTORY_PATH`; **claims via SQLite**, not HTTP |
| Deploy CLI | `ironflow deploy` | HTTP to API (`DeployClient`) |
| Persistence | JSONL + SQLite sidecar | Path from `IRONFLOW_HISTORY_PATH` (default `data/ironflow_history.jsonl`) |
| Native engine | Bundled in PyPI wheel `ironflow-prefect-compat` | Prefer wheel-based images over repo clone |
| Launcher | `scripts/ironflow_server.py` | Dev helper; starts uvicorn + optional Vite |

**Key constraint (today):** Server and standalone workers share a **local SQLite file** derived from `IRONFLOW_HISTORY_PATH`. Workers **claim via direct DB access**, not HTTP. Tier **B** removes this constraint; until B5 lands, only **B-fast** (shared volume) is available for split containers.

---

## 3. Prefect mapping (what we match vs defer)

| Prefect doc tier | Prefect behavior | IronFlow target |
| --- | --- | --- |
| **Minimal Docker** ([server-docker](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-docker)) | Single `prefecthq/prefect:3-latest` container, `--host 0.0.0.0`, UI on 4200 | **Tier A** — single `ironflow/server` image, API on 8000, optional bundled UI or separate static image |
| **Docker Compose** ([docker-compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose)) | Postgres + Redis + server + services + **HTTP worker** | **Tier B** — pre-steps (B0–B4) then production compose (B5); optional **B-fast** interim shared-volume compose |
| **Server CLI** ([server-cli](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli)) | SQLite default; Postgres + Redis for `--workers N` | **Tier B0–B1** for DB; document `ironflow server database upgrade` in B1 |
| **Security** ([security-settings](https://docs.prefect.io/v3/advanced/security-settings)) | `PREFECT_SERVER_API_AUTH_STRING` / `PREFECT_API_AUTH_STRING`; no OSS RBAC | **Tier C** — mirror env var names with `IRONFLOW_*` prefix |

**Explicit non-goals (this plan):**

- Prefect Cloud RBAC, tenants, audit, or API keys
- Full `prefect deploy` / blocks / recipe parity
- Kubernetes Helm chart (follow-up after compose is stable)

---

## 4. Design principles

1. **Wheel-first images** — base on `pip install ironflow-prefect-compat==<VERSION>`, not repo clone + `cargo build`, for consumer-facing tags.
2. **Honest tier labels** — docs must say when behavior differs from Prefect (shared-volume workers).
3. **Prefect-OSS security shape** — basic auth string first; OIDC via reverse-proxy guide, not built-in IdP.
4. **Additive env vars** — `IRONFLOW_*` settings; no breaking changes to default dev (`127.0.0.1`, open API).
5. **Tests per tier** — smoke script or CI job that builds images and hits `/health` (+ auth when enabled).
6. **Single-writer hotspots** — extend `server.py` via middleware module if auth grows; avoid unrelated refactors.

---

## 5. Phased delivery

### Tier A — Minimal Docker (Prefect [server-docker](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-docker) analog)

**Outcome:** One official image, one `docker run` line, documented in README / INSTALL.

| Task | Owner paths | Acceptance criteria |
| --- | --- | --- |
| A1. `deploy/docker/Dockerfile.server` | `deploy/docker/` | Multi-stage; Python 3.12-slim; installs pinned `ironflow-prefect-compat`; `CMD` uvicorn `0.0.0.0:8000` |
| A2. Entrypoint script | `deploy/docker/entrypoint-server.sh` | Sets sensible defaults: `IRONFLOW_HISTORY_PATH=/data/...`, creates `/data` dir |
| A3. Publish docs snippet | `docs/how-to/docker-quickstart.md` | Copy-paste `docker run -p 8000:8000 -v ironflow-data:/data ...` |
| A4. CI smoke (optional) | `.github/workflows/` | Build image on PR; `curl /health` — **ask first** per AGENTS hotspot rule |
| A5. Image naming | docs only | Document proposed tags: `ghcr.io/<org>/ironflow-server:<version>` |

**Default container profile:** embedded worker + scheduler **on** (dev-friendly, matches `ironflow_server.py start`).

**Example:**

```bash
docker run -p 8000:8000 -v ironflow-data:/data \
  -e IRONFLOW_HISTORY_PATH=/data/ironflow_history.jsonl \
  ghcr.io/<org>/ironflow-server:latest
```

---

### Tier B — Production compose (Prefect [docker-compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose) analog)

**Outcome:** `docker compose up` with Postgres, optional Redis, split server / background services / **HTTP-only workers**, optional UI — **no shared filesystem** between workers and server.

Tier B is **not** packaging-only. It requires control-plane changes first. Sub-phases **must ship in order** (B0 → B1 → … → B5), though B-fast can ship in parallel for early adopters.

#### B0 — Architecture RFC & storage abstraction

**Outcome:** Agreed design doc before schema/API work; clear migration path from file SQLite.

| Task | Owner paths | Acceptance criteria |
| --- | --- | --- |
| B0.1 RFC document | `docs/plans/self-hosted-storage-rfc.md` (or section in this plan) | Documents target topology, Postgres as primary store, fate of JSONL (audit log vs deprecated), Rust vs Python DB ownership |
| B0.2 Storage interface | `python-shim/.../persistence/` (new) | `ControlPlaneStore` protocol: deployments, runs, claims, events — SQLite backend implements it first (refactor, no behavior change) |
| B0.3 Connection config | env + docs | `IRONFLOW_DATABASE_URL` (Postgres DSN); keep `IRONFLOW_HISTORY_PATH` for file mode during transition |
| B0.4 Compatibility decision | `COMPATIBILITY.md` | Row: file SQLite = dev/single-node; Postgres = production compose |

**Design decisions to lock in B0:**

- **Postgres** as production store (align with Prefect); SQLite remains default for zero-config local dev.
- **JSONL:** keep as optional append-only audit/replay sidecar **or** migrate event writes to Postgres only — pick one in RFC.
- **Rust hot paths:** deployment claim, schedule tick, gate promotion — either Postgres via `tokio-postgres`/`sqlx` in `rust-engine` **or** HTTP boundary so workers never touch DB (Prefect model). **Recommended:** HTTP worker boundary + Postgres only on server/services (matches Prefect, simpler worker images).

---

#### B1 — Network database (Postgres)

**Outcome:** Server (and services process) use Postgres; migrations; export/import from file SQLite for upgrades.

| Task | Owner paths | Acceptance criteria |
| --- | --- | --- |
| B1.1 Schema migrations | `python-shim/.../migrations/` or Alembic | Versioned schema matching current SQLite tables (deployments, deployment_runs, workers, work_pools, flow_runs, …) |
| B1.2 Postgres store impl | `persistence/postgres.py`, `rust-engine/` if Rust reads DB | All API reads/writes work against Postgres when `IRONFLOW_DATABASE_URL` set |
| B1.3 Rust `bind_db` for Postgres | `rust-engine/src/` | Native claim/schedule paths use Postgres connection string, not file path — or defer claims to Python until B2 |
| B1.4 CLI database commands | `ironflow server database upgrade`, `reset` | Mirrors Prefect `prefect server database upgrade -y`; documented |
| B1.5 One-shot migrator | `ironflow server database migrate-from-files` | Optional: import existing JSONL+SQLite dir into Postgres |
| B1.6 Tests | `python-shim/tests/test_postgres_store.py` | CI job with Postgres service container (GitHub Actions `services.postgres`) |
| B1.7 Dev compose fragment | `deploy/docker/compose.postgres.yml` | Postgres service only; server pointed at it for integration tests |

**Env vars (proposed):**

| Variable | Purpose |
| --- | --- |
| `IRONFLOW_DATABASE_URL` | `postgresql://user:pass@host:5432/ironflow` |
| `IRONFLOW_DATABASE_MIGRATE_ON_START` | `true`/`false` (default `true` in containers) |
| `IRONFLOW_DATABASE_TIMEOUT` | Migration timeout seconds (Prefect: `PREFECT_SERVER_DATABASE_TIMEOUT`) |

**Docs (B1):** see §13 Documentation matrix — rows for database, env-vars, migration, troubleshooting.

---

#### B2 — HTTP worker protocol

**Outcome:** Workers never open the control-plane DB; all claim/heartbeat/finish traffic goes through the API (like Prefect).

| Task | Owner paths | Acceptance criteria |
| --- | --- | --- |
| B2.1 API routes | `server.py` or `routes/workers.py` | `POST /api/workers/claim`, `POST /api/workers/runs/{id}/started`, `…/finished`, extend heartbeat |
| B2.2 Idempotent claim semantics | `rust-engine/` + runtime | Same lease/concurrency rules as today; documented race behavior |
| B2.3 Worker client mode | `worker.py`, CLI | `IRONFLOW_WORKER_MODE=http` (default in containers); `file` mode for legacy/dev |
| B2.4 CLI `worker start` | `cli/main.py` | HTTP mode: only needs `IRONFLOW_API_URL` + auth; no `IRONFLOW_HISTORY_PATH` |
| B2.5 Flow execution payload | API contract | Claim response includes deployment entrypoint, resolved parameters, deployment_run id |
| B2.6 Tests | `test_http_worker.py` | Multi-worker claim exclusivity; lease expiry reclaim; work_pool filter |
| B2.7 Deprecation path | docs | Document when `file` worker mode is removed (semver major?) |

**Target worker loop (HTTP mode):**

```
worker → POST /api/workers/heartbeat
       → POST /api/workers/claim  (blocks or long-poll)
       → import entrypoint & run flow
       → POST /api/workers/runs/{id}/started|finished
```

---

#### B3 — Background services split

**Outcome:** Scheduler/maintenance runs in a **separate container** (Prefect `prefect server services start`).

| Task | Owner paths | Acceptance criteria |
| --- | --- | --- |
| B3.1 Services entrypoint | `ironflow server services start` or module | Runs scheduler loop only; no HTTP listener |
| B3.2 Server flag | env | `IRONFLOW_ENABLE_SCHEDULER=0` on API container; `1` on services container |
| B3.3 Single-leader scheduler | Postgres advisory lock or lease row | Only one services instance runs ticks in HA compose |
| B3.4 Health | `/health` on services optional | Liveness: last tick timestamp exposed via metrics or HTTP sidecar |
| B3.5 Tests | integration | Two services containers → only one active tick |

---

#### B4 — Redis & multi-worker API (optional until scale needed)

Prefect requires Redis when running `prefect server start --workers N`. Defer until IronFlow exposes multi-worker uvicorn.

| Task | Owner paths | Acceptance criteria |
| --- | --- | --- |
| B4.1 Redis lease / messaging | TBD after B2 | Concurrency lease storage if not fully in Postgres |
| B4.2 `ironflow server start --workers N` | CLI + uvicorn | Document parity with Prefect server-cli |
| B4.3 Compose Redis service | `compose.yml` | Add when B4.1 implemented |

**Trigger:** API CPU saturation or explicit HA requirement for API replicas.

---

#### B5 — Production Docker Compose stack

**Outcome:** Official `deploy/docker/compose.yml` matching Prefect’s five-service layout.

| Task | Owner paths | Acceptance criteria |
| --- | --- | --- |
| B5.1 Images | `deploy/docker/Dockerfile.{server,worker,ui,services}` | Wheel-based; server uses `--host 0.0.0.0` |
| B5.2 `compose.yml` | `deploy/docker/` | `postgres`, optional `redis`, `server`, `services`, `worker`, optional `ui` |
| B5.3 Worker service | compose | `IRONFLOW_API_URL=http://server:8000`, `IRONFLOW_WORKER_MODE=http`, no shared app volume |
| B5.4 UI service | `Dockerfile.ui` | `VITE_API_BASE`; nginx reverse proxy optional |
| B5.5 CORS / proxy | `server.py`, docs | `IRONFLOW_CORS_ORIGINS`, `IRONFLOW_UI_API_URL` for external URLs |
| B5.6 Healthchecks | compose | `/health` + DB connectivity |
| B5.7 Examples | `deploy/docker/examples/` | User flow image `FROM ironflow-worker`; sample `ironflow.yaml` |
| B5.8 E2E smoke | `scripts/docker_compose_smoke.sh` | Full path: migrate → deploy → trigger → completed run |
| B5.9 Auth overlay | `compose.auth.yml` | Tier C env vars wired |

**Production topology (target):**

```
                    ┌─────────────┐
  CLI / CI ──HTTP──►│   server    │───┐
                    │  (API only) │   │  IRONFLOW_DATABASE_URL
                    └─────────────┘   │
                           ▲          ▼
                           │    ┌──────────┐
                           │    │ postgres │
                           │    └──────────┘
                    ┌──────┴──────┐   ▲
                    │  services   │───┘  (scheduler / maintenance)
                    └─────────────┘

  ┌─────────────┐     HTTP claim/heartbeat
  │  worker(s)  │────────────────────────► server
  │ + user code │
  └─────────────┘

  ┌─────────────┐     HTTP (UI)
  │  ui (opt)   │────────────────────────► server
  └─────────────┘
```

**Depends on:** B1 (Postgres), B2 (HTTP workers), B3 (services split). B4 optional.

---

#### B-fast — Interim compose (shared volume, optional parallel track)

**Outcome:** Split server/worker containers **before** B1–B5 complete — for early testing only.

| Task | Owner paths | Acceptance criteria |
| --- | --- | --- |
| B-fast.1 `compose.shared-volume.yml` | `deploy/docker/` | Shared `ironflow-data:/data`; `IRONFLOW_ENABLE_LOCAL_WORKER=0` on server |
| B-fast.2 Docs warning | `docs/how-to/docker-compose-interim.md` | Bold: not production; NFS unsupported; superseded by B5 |

Use **only** when B2 is not ready; **do not** label as Prefect parity.

---

### Tier C — Basic auth (Prefect [security-settings](https://docs.prefect.io/v3/advanced/security-settings) analog)

**Outcome:** Optional shared-secret basic auth on API; CLI and UI support.

| Task | Owner paths | Acceptance criteria |
| --- | --- | --- |
| C1. Auth middleware | `python-shim/src/prefect_compat/auth.py`, wire in `server.py` | When `IRONFLOW_SERVER_API_AUTH_STRING` set, require `Authorization: Basic …` on `/api/*`; exempt `/health` |
| C2. Client auth | `deploy/client.py`, CLI | `IRONFLOW_API_AUTH_STRING` → Basic header on all HTTP calls |
| C3. UI auth | `frontend/src/api.ts` | Prompt or env for auth string; attach header; sessionStorage optional |
| C4. Compose example | `deploy/docker/compose.auth.yml` overlay | Secrets via env file (document `.env.example`, gitignored `.env`) |
| C5. Tests | `python-shim/tests/test_api_auth.py` | 401 without creds; 200 with creds; `/health` still open |
| C6. Matrix + docs | `COMPATIBILITY.md`, `docs/how-to/secure-self-hosted.md` | Row: “Self-hosted basic auth — subset parity with Prefect OSS”; reverse-proxy OIDC section |
| C7. CSRF | defer | Prefect has CSRF toggles; defer until UI cookie/session needs it |

**Env var mapping:**

| Prefect | IronFlow |
| --- | --- |
| `PREFECT_SERVER_API_AUTH_STRING` | `IRONFLOW_SERVER_API_AUTH_STRING` |
| `PREFECT_API_AUTH_STRING` | `IRONFLOW_API_AUTH_STRING` |

---

## 6. Suggested implementation order

```
Tier A (minimal single-container image)
    → Tier C (basic auth — can overlap with A)

Tier B (production compose — sequential pre-steps):
    B0 RFC + storage abstraction
    → B1 Postgres + migrations + CLI database commands
    → B2 HTTP worker protocol
    → B3 Background services split
    → B5 Production compose (+ Tier C auth overlay)
    → B4 Redis / multi-worker API (when needed)

Parallel (optional, labeled interim):
    B-fast shared-volume compose — only if B2 blocked; not production
```

**Recommended first PR:** Tier **A + C1/C2** (secured single-container server).

**Recommended first Tier B PR:** **B0 RFC + B0.2 storage interface refactor** (no user-visible behavior change).

**Tier B is “complete” when B5 smoke passes** — not when B-fast alone works.

---

## 7. Acceptance criteria (plan complete)

The plan is **done** when:

**Tier A + C**
- [ ] Official server Docker image + `docs/how-to/docker-quickstart.md`
- [ ] `IRONFLOW_SERVER_API_AUTH_STRING` / `IRONFLOW_API_AUTH_STRING` implemented and tested
- [ ] `docs/how-to/secure-self-hosted.md` covers basic auth + reverse-proxy OIDC

**Tier B (production compose)**
- [ ] B0 RFC approved; storage abstraction merged
- [ ] Postgres backend + `ironflow server database upgrade` + migration tests in CI
- [ ] HTTP worker mode default in containers; file mode documented as dev-only
- [ ] `ironflow server services start` (or equivalent) with single-leader scheduler
- [ ] `deploy/docker/compose.yml` (B5) with postgres + server + services + worker (+ optional ui)
- [ ] No shared filesystem required between workers and server
- [ ] `scripts/docker_compose_smoke.sh` passes against B5 stack
- [ ] Documentation matrix (§13) rows landed

**Explicitly not required for plan complete:** B4 Redis, Helm chart, Prefect Cloud RBAC

---

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| SQLite on NFS / cloud volumes corrupts or locks | B-fast docs forbid NFS; B1 Postgres is the fix |
| Users assume Prefect-compose parity before B5 | Label B-fast “interim”; publish topology diagram only for B5 |
| Postgres + Rust claim path divergence | B0 RFC picks single owner; prefer HTTP boundary (B2) to avoid dual DB writers |
| JSONL vs Postgres dual-write complexity | B0 RFC decides: audit sidecar vs full migration |
| Auth breaks UI SSE streams | Test `/api/stream/*` with auth header; document CORS + credentials |
| Worker image missing user flow code | B5 examples: `FROM ironflow-worker` + `ironflow serve` |
| Image drift from PyPI version | Pin `ironflow-prefect-compat==$VERSION` in Dockerfiles; release checklist in `RELEASING.md` |
| `server.py` hotspot conflicts | Auth in separate module; worker routes in `routes/workers.py` |
| Migration breaks existing single-node users | Keep file SQLite as default; Postgres opt-in via env; migrator tool (B1.5) |

---

## 9. Validation commands (per tier)

**Tier A:**

```bash
docker build -f deploy/docker/Dockerfile.server -t ironflow-server:local .
docker run --rm -p 8000:8000 -v /tmp/ironflow-data:/data \
  -e IRONFLOW_HISTORY_PATH=/data/ironflow_history.jsonl \
  ironflow-server:local
curl -sf http://127.0.0.1:8000/health
```

**Tier B5 compose:**

```bash
docker compose -f deploy/docker/compose.yml up -d
docker compose exec server ironflow server database upgrade -y
bash scripts/docker_compose_smoke.sh
docker compose -f deploy/docker/compose.yml down
```

**Tier B1 Postgres (CI / local):**

```bash
docker compose -f deploy/docker/compose.postgres.yml up -d
IRONFLOW_DATABASE_URL=postgresql://ironflow:ironflow@localhost:5432/ironflow \
  uv run pytest python-shim/tests/test_postgres_store.py
```

**Tier C:**

```bash
uv run pytest python-shim/tests/test_api_auth.py
# Manual: curl without auth → 401; with -u admin:pass → 200
```

**Regression (unchanged):**

```bash
uv run pytest python-shim/tests
cargo test --manifest-path rust-engine/Cargo.toml
```

---

## 10. PR / branch tracking

| PR / branch | Tier | Status | Notes |
| --- | --- | --- | --- |
| `cursor/self-hosted-docker-auth-plan-b5da` | — | Plan doc | Merged via parent work |
| `cursor/docker-tier-a-c-b5da` | A, C | **Merged** [#46](https://github.com/PPPSDavid/rust-based-prefect/pull/46) | Server image + basic auth |
| `cursor/tier-b-docker-plan-b5da` | B | Plan | Executable Tier B plan |
| *(pending)* | B0 | Not started | RFC + storage abstraction — see [tier-b plan](self-hosted-docker-tier-b.md) |
| *(pending)* | B1 | Not started | Postgres + migrations |
| *(pending)* | B2 | Not started | HTTP worker protocol |
| *(pending)* | B3 | Not started | Services split |
| *(pending)* | B4 | Not started | Redis / multi-worker API (optional) |
| *(pending)* | B5 | Not started | Production compose + CI |
| *(pending)* | B-fast | Skipped by default | Interim shared-volume compose (optional) |

Update this table as work lands.

---

## 11. Related docs to update when implementing

See **§13 Documentation matrix** for the full checklist. Minimum cross-links:

- `docs/SELF_HOSTED_SERVER.md` — link all how-to guides; split “dev file mode” vs “production compose”
- `docs/MEMORY_BANK.md` — status line when tiers land
- `RELEASING.md` — image publish + migration notes on major bumps

---

## 12. Open questions

1. **GHCR vs Docker Hub** — default publish registry for official images?
2. **Embed UI in server image** — nginx sidecar in server container vs always-separate `Dockerfile.ui`?
3. **JSONL fate** — keep as audit log alongside Postgres, or drop after B1?
4. **Rust Postgres vs HTTP-only workers** — does `rust-engine` connect to Postgres directly, or only server Python + HTTP for workers? (RFC B0)
5. **B-fast** — ship at all, or skip straight to B5 pre-steps?
6. **Helm chart** — community contribution or first-party after B5 stabilizes?

Resolve in B0 RFC; do not block Tier A on these.

---

## 13. Documentation matrix

Every Tier B pre-step should land **user-facing or reference docs in the same PR** (or a immediate follow-up). `docs/plans/**` stays internal; items below are published via MkDocs unless noted.

| Doc | When | Content to add |
| --- | --- | --- |
| **`docs/how-to/docker-quickstart.md`** | Tier A | Single-container `docker run`; volume for file SQLite; link to B5 for production |
| **`docs/how-to/docker-compose.md`** | Tier B5 | Full compose stack; env file; healthchecks; worker scaling; **primary production guide** |
| **`docs/how-to/docker-compose-interim.md`** | B-fast only | Shared-volume split; warnings; link to B5 |
| **`docs/how-to/secure-self-hosted.md`** | Tier C | Basic auth env vars; `.env.example`; reverse-proxy + OIDC (Traefik/nginx); no RBAC claims |
| **`docs/how-to/database-postgres.md`** | Tier B1 | `IRONFLOW_DATABASE_URL`; migrate from files; backup/restore basics |
| **`docs/how-to/run-background-services.md`** | Tier B3 | `ironflow server services start`; compose service definition; HA notes |
| **`docs/how-to/worker-http-mode.md`** | Tier B2 | HTTP vs file worker; env vars; no shared volume |
| **`docs/reference/env-vars.md`** | A, C, B* | All new `IRONFLOW_*` vars with defaults |
| **`docs/reference/api.md`** | B2, C | Worker claim routes; auth on `/api/*`; remove “no authentication” |
| **`docs/reference/troubleshooting.md`** | B* | Postgres connection; worker 401; claim stuck; NFS/SQLite warnings |
| **`docs/SELF_HOSTED_SERVER.md`** | All | Restructure: Dev (file) / Docker quickstart / Production compose / Security |
| **`docs/architecture.md`** | B0, B1 | Persistence diagram: JSONL, SQLite dev, Postgres prod, HTTP workers |
| **`docs/PREFECT_IRONFLOW_MAPPING.md`** | B5, C | Rows: Docker, Postgres, HTTP workers, basic auth, services split |
| **`COMPATIBILITY.md`** | Each tier | Matrix rows per feature; no parity claims without tests |
| **`README.md`** | A, B5 | Docker one-liner; link to compose guide |
| **`docs/INSTALL.md`** | A | Container install path alongside pip |
| **`docs/concepts/runners.md`** | B2 | Note worker process vs task runner distinction |
| **`docs/agent/GOLDEN_PATHS.md`** | B5 | Agent validation: compose smoke script |
| **`RELEASING.md`** | A, B5 | GHCR publish workflow; pin wheel version in images |
| **`CHANGELOG.md`** | Each merge | User-visible deployment changes |
| **`docs/plans/self-hosted-storage-rfc.md`** | B0 | Internal/publish? — architecture RFC (link from architecture.md) |
| **MkDocs nav** (`mkdocs.yml`) | When how-tos added | New pages under **How-to guides → Self-hosted** |

### Documentation principles

1. **Two deployment modes** everywhere: **Dev (file SQLite)** vs **Production (Postgres compose)** — never blur them.
2. **Prefect doc links** as comparison anchors, not parity claims.
3. **Operations section** in compose guide: upgrade, backup Postgres, rotate auth string, add worker replica.
4. **Migration guide** for existing `data/ironflow_history.jsonl` users upgrading to B1.

---
