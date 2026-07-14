# Tier B — Executable delivery plan (4 PRs)

**Status:** In progress (B2)  
**Last updated:** 2026-07-14  
**RFC:** [self-hosted-storage-rfc.md](self-hosted-storage-rfc.md)  
**Tracking overview:** [self-hosted-docker-auth.md](self-hosted-docker-auth.md)

Collapse the earlier B0–B5 checklist into **four shippable PRs**. Prefer this document for sequencing; keep the tracking plan for Prefect mapping and docs matrix.

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
| **3 — B2** | `cursor/tier-b2-http-workers-*` | Claim / started / finished HTTP API; worker mode without file DB | This PR |
| **4 — B3/B5** | `cursor/tier-b-compose-*` | Compose file(s), images, GHA compose smoke, GHCR | Pending |

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

**Deferred**

- Services process split (B3) and production compose (B5)
- Removing `file` worker mode (semver major)

