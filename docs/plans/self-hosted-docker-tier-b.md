# Tier B — Executable delivery plan (4 PRs)

**Status:** Complete (core) — B0–B3/B5 merged (#49/#52/#56/#57)  
**Last updated:** 2026-07-25  
**RFC:** [self-hosted-storage-rfc.md](self-hosted-storage-rfc.md)  
**Tracking overview:** [self-hosted-docker-auth.md](self-hosted-docker-auth.md)

Collapse the earlier B0–B5 checklist into **four shippable PRs**. Prefer this document for sequencing; keep the tracking plan for Prefect mapping and docs matrix.

**Prefect guides to borrow / enhance from** (structure and operator expectations, not parity claims):

- [Local Prefect server (CLI)](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli)
- [Prefect server in Docker](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-docker)
- [Prefect Server via Docker Compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose)
- [Secure a self-hosted Prefect server](https://docs.prefect.io/v3/advanced/security-settings)

## Non-negotiables

1. **Rust owns hot paths** — claim, schedule tick, gates, FSM. Postgres must be usable from `rust-engine` (B1), not Python-only forever.
2. **HTTP workers for multi-host** — no shared SQLite volume in production compose (B2+).
3. **Docker compose E2E runs in GitHub Actions** (Cloud agents may not have Docker).
4. **Skip B-fast** (shared-volume compose) and **defer Redis** unless product asks.

## PR map

| PR | Branch pattern | Outcome | Acceptance |
| --- | --- | --- | --- |
| **1 — B0** | `cursor/tier-b0-store-*` | Storage RFC + `persistence/` SQLite extract | Merged (#49) |
| **2 — B1** | `cursor/tier-b1-postgres-*` | Postgres store + dialect adapter; Rust `bind_db` DSN + claim/lease; CI Postgres job | Merged (#52) |
| **3 — B2** | `cursor/tier-b2-http-workers-*` | Claim / started / finished HTTP API; worker mode without file DB | Merged (#56) |
| **4 — B3/B5** | `cursor/tier-b-compose-*` | Compose file(s), images, GHA compose smoke, how-to docs | Merged (#57) |

## PR 2 detail (B1)

**In scope**

- `PostgresStore` + `IRONFLOW_DATABASE_URL`
- SQLite→Postgres SQL dialect adapter for control-plane executes
- Rust `bind_db` with `database_url`; claim/mark/attach on Postgres (`FOR UPDATE SKIP LOCKED`)
- GHA `services.postgres` job + `test_postgres_store.py`
- Docs: env-vars, how-to, COMPATIBILITY row

**Deferred to follow-ups (still Rust destination)**

- Full Rust schedule tick / gate / ui_write on Postgres (Python fallback today via unknown-op)
- Alembic-style numbered migrations CLI (`ironflow server database upgrade`)
- One-shot migrator from JSONL/SQLite files

## PR 3 detail (B2)

**In scope**

- `POST /api/workers/claim`, `…/runs/{id}/started`, `…/finished` (+ heartbeat) in `routes/workers.py`
- Claim response enrichment with `deployment.flow_name` / `entrypoint`
- `WorkerHttpClient` + `IRONFLOW_WORKER_MODE=http` / `--worker-mode`
- CLI `worker start` / `serve` HTTP path (API URL + auth; no history path)
- Tests: `test_http_worker.py` (exclusivity, pool filter, lease reclaim, execute roundtrip)
- Docs: `docs/how-to/worker-http-mode.md`, env-vars, COMPATIBILITY

**Still open (follow-ups)**

- Removing `file` worker mode (semver major)

## PR 4 detail (B3/B5)

**In scope**

- `ironflow server services start` + `prefect_compat.services.run_services_loop`
- `Dockerfile.{server,services,worker}` with Postgres extra where needed
- `deploy/docker/compose.yml` (+ optional `compose.auth.yml`)
- `scripts/docker_compose_smoke.sh` + GHA workflow
- Docs: docker-compose, run-background-services, COMPATIBILITY

**Deferred**

- HA advisory lock for multi-services
- Redis / UI image / GHCR publish automation
- Full Alembic migrator CLI
