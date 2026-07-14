# Tier B — Executable delivery plan (4 PRs)

**Status:** In progress (B0)  
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
| **1 — B0** | `cursor/tier-b0-store-*` | Storage RFC + `persistence/` SQLite extract; control plane behavior unchanged | Unit tests for `SqliteStore` / factory; deploy + worker + auth pytest green |
| **2 — B1** | `cursor/tier-b1-postgres-*` | Migrations, Postgres store, Rust `bind_db` for Postgres DSN, CI Postgres service | `IRONFLOW_DATABASE_URL` path; rust + python tests against Postgres |
| **3 — B2** | `cursor/tier-b2-http-workers-*` | Claim / started / finished HTTP API; worker mode without file DB | Multi-process tests; file mode still works for local |
| **4 — B3/B5** | `cursor/tier-b-compose-*` | Compose file(s), images, GHA compose smoke, GHCR | `docker compose up` smoke in Actions; docs for prod path |

Optional later: B4 Redis, HA services split polish, migrator CLI enhancements.

## PR 1 detail (this change)

**In scope**

- `docs/plans/self-hosted-storage-rfc.md`
- `docs/plans/self-hosted-docker-tier-b.md` (this file)
- `python-shim/src/prefect_compat/persistence/`
- Wire `InMemoryControlPlane` via `create_store`
- Index updates (`docs/plans/README.md`, `docs/MEMORY_BANK.md`)

**Out of scope**

- Postgres implementation
- HTTP worker routes
- Compose / GHCR
- `COMPATIBILITY.md` production-Postgres claims (wait for B1)

## Handoff notes

- Postgres 16 may be available in Cloud (`127.0.0.1:5432`) for B1 experimentation; compose smoke still needs GHA.
- Hotspots: `runtime.py`, `server.py`, `rust-engine` bind/claim modules — one writer per PR.
