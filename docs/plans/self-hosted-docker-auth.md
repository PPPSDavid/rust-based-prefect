# Self-Hosted Docker & Access Control Plan (IronFlow)

**Status:** Draft — not started  
**Last updated:** 2026-07-12  
**Scope:** `deploy/docker/`, `python-shim/`, `frontend/`, `scripts/`, `docs/`, `COMPATIBILITY.md`  
**User-facing docs (target):** `docs/how-to/docker-compose.md`, `docs/how-to/secure-self-hosted.md`, updates to `docs/SELF_HOSTED_SERVER.md`  
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

**Goal:** Ship **Tier A–C** (below) so a team can `docker compose up` and run flows with basic auth — then track **Tier D** for Prefect-compose parity (HTTP workers + network DB).

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

**Key constraint:** Until Tier D, **server and all workers must share the same history volume**. This is intentional interim behavior, not Prefect-equivalent.

---

## 3. Prefect mapping (what we match vs defer)

| Prefect doc tier | Prefect behavior | IronFlow target |
| --- | --- | --- |
| **Minimal Docker** ([server-docker](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-docker)) | Single `prefecthq/prefect:3-latest` container, `--host 0.0.0.0`, UI on 4200 | **Tier A** — single `ironflow/server` image, API on 8000, optional bundled UI or separate static image |
| **Docker Compose** ([docker-compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose)) | Postgres + Redis + server + services + **HTTP worker** | **Tier B1** interim (shared volume); **Tier D** for true parity |
| **Server CLI** ([server-cli](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli)) | SQLite default; Postgres + Redis for `--workers N` | Document SQLite/file model today; Tier D for Postgres |
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

### Tier B1 — Compose stack (interim, shared volume)

**Outcome:** `docker compose up` with split server / worker(s) / optional UI — **works with today's SQLite claim path**.

| Task | Owner paths | Acceptance criteria |
| --- | --- | --- |
| B1.1 `deploy/docker/Dockerfile.worker` | `deploy/docker/` | Same base as server; `CMD` `ironflow worker start` or `ironflow serve` |
| B1.2 `deploy/docker/Dockerfile.ui` | `deploy/docker/`, `frontend/` | nginx + `npm run build`; `VITE_API_BASE` build arg |
| B1.3 `deploy/docker/compose.yml` | `deploy/docker/` | Services: `server`, `worker`, optional `ui`; shared volume `ironflow-data:/data` |
| B1.4 Server env in compose | compose | `IRONFLOW_ENABLE_LOCAL_WORKER=0`; scheduler on |
| B1.5 Worker mount pattern | compose + docs | User flow code at `/app/flows`; sample `ironflow.yaml` in `deploy/docker/examples/` |
| B1.6 CORS env var | `python-shim/.../server.py` | `IRONFLOW_CORS_ORIGINS` (comma-separated); default keeps current localhost:4173 |
| B1.7 Bind / API URL docs | `docs/how-to/docker-compose.md` | Document `IRONFLOW_API_URL` for CLI from host vs inside compose network |
| B1.8 E2E smoke script | `scripts/docker_compose_smoke.sh` | `compose up`, `ironflow deploy`, trigger run, assert terminal state |

**Compose topology (interim):**

```
┌─────────────┐     HTTP (deploy only)      ┌─────────────┐
│   server    │◄────────────────────────────│  CLI / CI   │
│  scheduler  │                             └─────────────┘
│  (no worker)│
└──────┬──────┘
       │ shared volume: IRONFLOW_HISTORY_PATH (JSONL + SQLite)
       ├──────────────────┬──────────────────┐
       ▼                  ▼                  ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  worker 1   │    │  worker 2   │    │  ui (opt)   │
│ SQLite claim│    │ SQLite claim│    │  → server   │
└─────────────┘    └─────────────┘    └─────────────┘
```

**Documentation callout (required):** “Workers read the control-plane database directly. All worker containers must mount the same `IRONFLOW_HISTORY_PATH` volume. This differs from Prefect’s HTTP-only worker model.”

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

### Tier D — Prefect-compose parity (future / separate plan revision)

**Outcome:** Workers claim over HTTP; network database; optional Redis — **no shared filesystem requirement**.

| Capability | Work estimate | Dependencies |
| --- | --- | --- |
| D1. HTTP worker protocol | New routes: claim, renew lease, report started/finished, heartbeat | Rust + Python; idempotent claim semantics |
| D2. Postgres (or other network DB) | Replace / supplement file SQLite | Schema migrations, connection pooling |
| D3. Split “background services” | Optional separate container for scheduler-only | Env to disable scheduler in API process |
| D4. Redis (if multi-worker API) | Messaging / lease store | Only if horizontal API scaling is required |
| D5. `ironflow server database upgrade` | CLI for migrations | D2 |

**Trigger to start Tier D:** A user needs workers on **different hosts** without shared storage, or API replicas > 1.

Track as **follow-up plan** or Phase 2 section once B1 + C ship.

---

## 6. Suggested implementation order

```
Tier A (minimal image)
    → Tier C1–C3 (auth middleware + clients)   # can parallelize with B1 after A
    → Tier B1 (compose + CORS + UI image)
    → Tier C4–C6 (auth compose overlay + docs + tests)
    → Tier D (RFC / separate plan)
```

**Recommended first PR:** Tier A + C1/C2 (server image + auth middleware + CLI) — smallest vertical slice for “secured containerized server.”

---

## 7. Acceptance criteria (plan complete)

The plan is **done** when:

- [ ] Official server (and worker) Dockerfiles exist under `deploy/docker/`
- [ ] `deploy/docker/compose.yml` starts server + worker + optional UI with documented shared volume
- [ ] `docs/how-to/docker-quickstart.md` and `docs/how-to/docker-compose.md` exist and link from `SELF_HOSTED_SERVER.md`
- [ ] `IRONFLOW_SERVER_API_AUTH_STRING` / `IRONFLOW_API_AUTH_STRING` implemented and tested
- [ ] `docs/how-to/secure-self-hosted.md` covers basic auth + reverse-proxy OIDC pattern
- [ ] `COMPATIBILITY.md` updated (no RBAC claims)
- [ ] Smoke script or CI proves `compose up` + `/health` (+ auth path)
- [ ] Tier D captured as explicit follow-up with RFC stub or linked issue

---

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| SQLite on NFS / cloud volumes corrupts or locks | Document “local volume or bind mount only” for B1; Tier D for network DB |
| Users assume Prefect-compose parity | Bold “interim” label in compose docs; comparison table in §3 |
| Auth breaks UI SSE streams | Test `/api/stream/*` with auth header; document CORS + credentials |
| Worker image missing user flow code | Document `ironflow serve` pattern; example Dockerfile `FROM ironflow-worker` |
| Image drift from PyPI version | Pin `ironflow-prefect-compat==$VERSION` in Dockerfiles; release checklist in `RELEASING.md` |
| `server.py` hotspot conflicts | Auth in separate module; single PR owner per AGENTS single-writer rule |

---

## 9. Validation commands (per tier)

**Tier A / B1:**

```bash
docker build -f deploy/docker/Dockerfile.server -t ironflow-server:local .
docker run --rm -p 8000:8000 -v /tmp/ironflow-data:/data \
  -e IRONFLOW_HISTORY_PATH=/data/ironflow_history.jsonl \
  ironflow-server:local
curl -sf http://127.0.0.1:8000/health
```

**Tier B1 compose:**

```bash
docker compose -f deploy/docker/compose.yml up -d
bash scripts/docker_compose_smoke.sh
docker compose -f deploy/docker/compose.yml down
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
| `cursor/self-hosted-docker-auth-plan-b5da` | — | **This plan doc** | Tracking only |
| *(pending)* | A | Not started | Minimal server image |
| *(pending)* | C | Not started | Basic auth |
| *(pending)* | B1 | Not started | Compose stack |
| *(pending)* | D | Not started | RFC: HTTP workers + Postgres |

Update this table as work lands.

---

## 11. Related docs to update when implementing

- `docs/SELF_HOSTED_SERVER.md` — link Docker guides
- `docs/reference/env-vars.md` — new `IRONFLOW_*` vars
- `docs/reference/api.md` — remove “no authentication” when Tier C ships
- `docs/PREFECT_IRONFLOW_MAPPING.md` — Docker + auth rows
- `README.md` — Quickstart Docker one-liner
- `docs/MEMORY_BANK.md` — one-line status when tiers land
- `RELEASING.md` — image publish steps (when GHCR workflow added)

---

## 12. Open questions

1. **GHCR vs Docker Hub** — default publish registry for official images?
2. **Embed UI in server image** — nginx sidecar in server container vs always-separate `Dockerfile.ui`?
3. **Tier D priority** — Postgres before HTTP workers, or HTTP workers first on existing SQLite (single-server)?
4. **Helm chart** — community contribution or first-party after compose stabilizes?

Resolve in first implementation PR discussion; do not block Tier A on these.
