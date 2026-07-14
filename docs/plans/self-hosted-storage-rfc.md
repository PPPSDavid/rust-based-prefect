# Self-Hosted Storage RFC (Tier B0)

**Status:** Accepted for implementation  
**Last updated:** 2026-07-14  
**Depends on:** Tier A (server Docker) + Tier C (basic auth) on `main`  
**Related:** `docs/plans/self-hosted-docker-auth.md`, `docs/plans/self-hosted-docker-tier-b.md`

## 1. Goal

Unlock production compose (Postgres + multi-host HTTP workers) without abandoning IronFlow’s performance model: **claim, schedule tick, gate promotion, and FSM hot paths stay in `rust-engine`**. Python remains HTTP glue and store wiring.

## 2. Topology (target)

```
┌────────────┐     HTTP (+ basic auth)     ┌──────────────────┐
│  Workers   │ ◄──────────────────────────► │  API server      │
│  (stateless│   claim / started / finished │  FastAPI         │
│   images)  │                              │       │          │
└────────────┘                              │       ▼          │
                                            │  rust-engine FFI │
                                            │  (claim/schedule │
                                            │   /gates/FSM)    │
                                            │       │          │
                                            └───────┼──────────┘
                                                    ▼
                                         ┌──────────────────┐
                                         │  Postgres (prod) │
                                         │  or SQLite (dev) │
                                         └──────────────────┘
```

- **Dev / single-node:** SQLite file derived from `IRONFLOW_HISTORY_PATH` (today’s default). Optional embedded worker.
- **Production compose:** Postgres via `IRONFLOW_DATABASE_URL`; workers talk **only** to the API (no shared volume, no direct DB).

## 3. Decisions

| Topic | Decision |
| --- | --- |
| Production store | **Postgres** |
| Local default | **SQLite** (unchanged zero-config) |
| Rust ownership | Hot paths **must** bind to the active store (SQLite path today; Postgres DSN in B1). Python does **not** become the long-term claim/schedule owner. |
| HTTP workers | Required for multi-host (B2). API routes call Rust FFI against the server-owned DB. |
| JSONL history | Keep as **optional append-only audit/replay sidecar** for file mode; not required for Postgres mode. |
| Redis | **Deferred** (B4); leases/schedules live in Postgres first. |
| Dual writers | Avoid. Only the API/services process opens the DB; workers are HTTP clients. |

### Explicit rejection

“Python-only Postgres forever with Rust limited to SQLite” is **not** an acceptable product destination. A temporary Python migration path during B1 is fine; B1 acceptance requires Rust claim/schedule/gates against Postgres (or a documented interim with a hard follow-up PR in the same Tier B sequence).

## 4. Storage abstraction (B0)

Package: `python-shim/src/prefect_compat/persistence/`

| Module | Role |
| --- | --- |
| `protocol.py` | `ControlPlaneStore` — `backend_kind`, `path`, `connection`, `ensure_schema`, `close` |
| `store_sqlite.py` | Extracted SQLite open + schema DDL + additive upgrades |
| `factory.py` | `create_store()`; Postgres DSN → `NotImplementedError` until B1 |
| `constants.py` | `DEFAULT_WORK_POOL_ID` |

`InMemoryControlPlane` holds `_store` and continues exposing `_sqlite_conn` / `_sqlite_path` for tests and Rust `bind_db`. Query/mutation SQL stays on the control plane until B1 migrates call sites behind the store.

## 5. Env vars

| Variable | Mode | Notes |
| --- | --- | --- |
| `IRONFLOW_HISTORY_PATH` | File / SQLite | Existing; `.db` sidecar beside JSONL |
| `IRONFLOW_DATABASE_URL` | Postgres (B1+) | `postgresql://…`; reserved in B0 factory |
| `IRONFLOW_SERVER_API_AUTH_STRING` / `IRONFLOW_API_AUTH_STRING` | All networked | Already shipped (Tier C) |

## 6. Compatibility stance

| Mode | Audience | Support |
| --- | --- | --- |
| SQLite + shared file workers | Local / single host | Supported; document limits |
| Postgres + HTTP workers | Production compose | Target after B1–B2 |
| Shared SQLite over NFS | — | Unsupported |

Update `COMPATIBILITY.md` when B1 env vars become usable (not in B0 beyond RFC pointer).

## 7. Delivery sequence (four PRs)

See `docs/plans/self-hosted-docker-tier-b.md`.

1. **B0** — this RFC + SQLite extract (behavior-neutral) ← this PR  
2. **B1** — Postgres migrations + Python store + **Rust Postgres bind** + CI `services.postgres`  
3. **B2** — HTTP claim/started/finished worker protocol (Rust behind routes)  
4. **B3/B5** — compose images, GHA compose smoke, GHCR publish (Redis deferred)

Skip **B-fast** (shared-volume compose) unless explicitly requested.

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| Schema drift SQLite ↔ Postgres | Single migration source of truth in B1; golden schema tests |
| Rust Postgres glue delay | Block B1 merge on Rust bind (or same-train follow-up before B2) |
| Large `runtime.py` hotspot | B0 extract only schema open; further slice in B1 |
| Compose E2E needs Docker | Run in GitHub Actions; Cloud agents may lack Docker |

## 9. Acceptance (B0)

- [x] RFC checked in  
- [x] `persistence/` package with SQLite backend  
- [x] Control plane wires through `create_store` with unchanged SQLite behavior  
- [x] Focused store unit tests + existing deploy/worker/auth suites green  
