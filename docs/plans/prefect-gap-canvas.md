# Prefect ↔ IronFlow gap canvas (2026-07)

**Status:** Living backlog — **P0/P1/P3.0–P3.2e/f landed on `main`**; **P3.3–P3.5 remaining docs/smoke extras**, **P4.1+** still open (`P4.0` lease-on-cancel landed with airtightness harness)  
**Date:** 2026-07-25 (rev: P3 docs truth after #65)  
**Audience:** Maintainers choosing the next 1-feature sessions  
**Sources checked:**

| Source | Role |
| --- | --- |
| Hosted docs | https://pppsdavid.github.io/rust-based-prefect/ (+ `llms.txt`) |
| Local normative | `COMPATIBILITY.md`, `docs/PREFECT_IRONFLOW_MAPPING.md`, `docs/compatibility_review_workflow.md` |
| Local how-tos / concepts | `docs/how-to/*`, `docs/concepts/*`, `docs/plans/*`, `docs/MEMORY_BANK.md` |
| Prefect get-started | https://docs.prefect.io/v3/get-started |
| Prefect concepts / how-tos | https://docs.prefect.io/llms.txt (v3 concepts + how-to-guides) |
| Prefect OSS tree | https://github.com/PrefectHQ/prefect (`src/prefect/*` client orchestration surface) |
| IronFlow code | `python-shim/src/prefect_compat/`, `rust-engine/`, `frontend/`, `static-planner/` |
| In-flight | Open PR [#50](https://github.com/PPPSDavid/rust-based-prefect/pull/50) task resume / result cache |

Related workflow: [compatibility_review_workflow.md](../compatibility_review_workflow.md).

---

## How to read this

Not every Prefect difference is a gap. Each row is classified:

| Tag | Meaning |
| --- | --- |
| **gap** | Useful alignment candidate; missing or too thin for IronFlow’s stated goals |
| **partial** | Implemented subset; narrow follow-up is still valuable |
| **deliberate** | Intentional divergence (keep; document, do not “fix” toward Prefect) |
| **park** | Out of scope for the near-term MVP / wrong product shape |
| **docs** | Code exists (or is N/A) but published docs / matrix / sitemap lag |

Suggested session size: **one primary actionable ID per agent branch**.

---

## Snapshot: what IronFlow already covers well

Against Prefect 3’s get-started mental model (flows, tasks, deployments, schedules, workers, self-host):

- Authoring: `@flow` / `@task`, `submit` / `map` / `wait_for`, runners (thread / process / sequential)
- Control plane: Rust FSM, retries/timeouts/cancel intent, persistence (SQLite + Postgres subset), HTTP API + Vite UI
- Deploy path: `ironflow.yaml` CLI Tier 1, process work pools, HTTP workers, Docker / Compose
- Concurrency: deployment caps + global + tag limits + rate_limit (sync)
- Nesting: inline + `deployment_ref` subflows; IronFlow extensions `wait_all`, `gate`, `transition_hooks`
- Differentiator: static planner + forecast + logical/expanded DAG UI

IronFlow is **not** aiming at Prefect Cloud, the integration marketplace, or full REST/CLI parity.

---

## Deliberate differences (do not treat as defects)

| Area | Prefect | IronFlow choice | Why keep |
| --- | --- | --- | --- |
| Orchestration kernel | Python services | Rust `rust-engine` | Determinism + hot-path perf |
| Import surface | `prefect` | `prefect_compat` | Independent runtime |
| Lifecycle hooks | `on_running` / `on_failure` / … | `transition_hooks` / `on_transition` edges | Explicit FSM edges |
| Flow terminal state | Return value / `State` objects | Default `final_state="wait_all"` (+ `detach` / `explicit`) | Concurrent-submit correctness |
| Calendar waits | Pause / input / automations | First-class `gate(...)` task | Deterministic in-flow barrier |
| Static planning | Runtime-dynamic first | `static-planner` forecast + UI modes | Pre-run visibility |
| Auth | Cloud RBAC / SSO | Optional HTTP Basic only | Self-hosted MVP |
| Work pools | Many infra types | Process (+ HTTP claim) | Avoid infra sprawl |
| Blocks / integrations | Large ecosystem | Not a focus | Wrong layer for Rust-first core |
| Deploy recipes | `prefect.yaml` + many pull steps | `ironflow.yaml` + `set_working_directory` | Narrow deploy Tier 1 |

---

## Gap table (Prefect concept → IronFlow)

Prefect concept pages from https://docs.prefect.io/v3/concepts (plus adjacent get-started themes).

| Prefect surface | IronFlow today | Class | Notes |
| --- | --- | --- | --- |
| Flows | Supported subset + docs | partial | Missing async flows, Prefect `State` return model (deliberate via `wait_all`) |
| Tasks | Supported subset + docs | partial | No `task.delay()`, thin caching/resume on `main` |
| Task runners | Thread / process / sequential | partial | No Dask/Ray — **park** |
| States | Rust `RunState` FSM | partial | Edge-case parity unfinished; hooks API deliberate |
| Deployments | Create/patch/run/schedules/CLI | partial | No auto-deploy from `@flow`; pull steps minimal |
| Schedules | Interval / cron / RRule subset | partial | No `COUNT` / advanced calendar filters |
| Work pools / workers | Process pools + file/HTTP workers | partial | No Docker/K8s/push pools — **park** near-term |
| Work queues | Not really modeled | park | Explicit non-goal in UI checklist / GCL plan |
| Global concurrency | Sync CM + CRUD + leases | partial | Async CM, CLI `gcl`, UI admin still open |
| Tag concurrency | `@task(tags=...)` + `tag:` limits | partial | Same follow-ups as GCL |
| Caching / results | Artifact rows; retry re-runs all tasks on `main` | gap | Design+impl in flight on PR #50 |
| Results persistence | No public persist/result API on `main` | gap | Tied to resume / Goal B cache |
| Artifacts (user-facing) | Internal `artifact_type=result` metadata + GET APIs | partial | Not Prefect `create_markdown` / tables / links |
| Blocks | Unsupported | park | |
| Variables | None | gap (low) | Small JSON store candidate |
| Secrets / settings / profiles | Env vars only | park / docs | Document “use env + Basic auth”; no Prefect profiles |
| Runtime context | Implicit control-plane context | partial | No Prefect `runtime` module parity |
| Events | Control-plane events + SSE | partial | Emit/query only; no automation consumers |
| Automations / triggers / webhooks | None | gap (design-first) | Large surface; subset later |
| Interactive pause / input | Operator pause `drain`/`terminate` + resume API; UI chooser shipped | partial (CLI open) → **P3.2e** | Human-input forms stay park; guide: `docs/how-to/cancel-pause-resume.md` |
| Logging (`get_run_logger`, `log_prints`) | `get_run_logger` → log store/UI; `log_prints=` open | partial | High DX value for porting |
| Assets / SLAs / telemetry | None | park | Cloud-leaning / advanced |
| Server / self-hosted scale | Compose shipped; HA/Redis deferred | partial | See Tier B follow-ups |
| UI | Runs/Flows/Deployments/Work pools + DAG | partial | Checklist stale; concurrency admin missing |
| MCP / AI assistants | IronFlow `llms.txt` only | docs / park | Prefect MCP is Cloud/OSS ops tooling |
| Integrations gallery | None | park | |

---

## Documentation / IA gaps (vs Prefect docs quality bar)

These are actionable even when the feature is “done.”

| ID | Finding | Severity |
| --- | --- | --- |
| D1 | `docs/gen_llms.py` sitemap lags `mkdocs.yml` / how-to index (Docker, Compose, Postgres, HTTP workers, auth, concurrency often absent from published `llms.txt`) | High for agents |
| D2 | `how-to/concurrency-limits.md` linked from how-to index but missing from `mkdocs.yml` nav | High |
| D3 | No published concept pages for deployments, schedules, work pools, concurrency, server — Prefect has first-class concept pages; IronFlow buries them in how-tos / SELF_HOSTED | Medium |
| D4 | `docs/ui_prefect_parity_checklist.md` still unchecked despite UI routes existing — checklist drift | Medium |
| D5 | `COMPATIBILITY.md` “Not yet supported” is shorter than the real backlog (resume, logging, artifacts UX, automations, variables, cooperative cancel) | High |
| D6 | Port guide does not walk common Prefect APIs that will fail (`get_run_logger`, blocks, `cache_policy`, `run_deployment`, Cloud connect) | Medium |
| D7 | Plans / MEMORY_BANK excluded from site (intentional) — fine, but public mapping should point at “open gaps” without maintainer-only files | Low |
| D8 | Hosted site vs `main` can lag until Pages rebuild — verify after doc merges | Process |

---

## Prioritization consensus (three-expert pass)

**Expert A — Porting / product:** Users bounce on retry recomputation, missing log helpers, and docs that don’t list real limits. After resume, **logging + cancel + concurrency ops surfaces** are the core Prefect day-2 experience — ahead of Postgres HA polish.

**Expert B — Rust / control plane:** Prefer gaps that strengthen deterministic hot paths (resume lineage + result store, GCL lease correctness under cancel, cooperative cancel signals). Avoid Python-only Prefect surface clones (blocks, profiles, integration zoo). Async GCL must stay thin over the same Rust acquire path.

**Expert C — Docs / discoverability:** Ship sitemap/nav/matrix hygiene first so every later session starts from the same truth; concept pages for concurrency/deployments land with or right after the runtime DX work.

**Consensus order (updated after review):** Docs hygiene (P0) → finish resume (P1) → **runtime DX core (P3)** → **concurrency polish (P4)** → self-hosted Rust/Postgres follow-ups (P2) → design-only bigger bets (P5/P7) → park Cloud/integrations.

---

## Sorted actionable backlog (proposed sessions)

Each item is sized for a **separate Cursor session / PR**. Adjust order after review.

### P0 — Docs truth & discoverability (1–2 sessions)

| ID | Action | Acceptance | Ownership |
| --- | --- | --- | --- |
| **P0.1** | Sync `mkdocs.yml` nav + `docs/gen_llms.py` with every published how-to (incl. concurrency, Docker, Postgres, workers, auth) | Hosted `llms.txt` lists all nav pages; concurrency in Material nav | docs *(repo fix in the canvas PR; confirm after Pages rebuild)* |
| **P0.2** | Expand `COMPATIBILITY.md` “Not yet supported” + mapping table rows for resume, logging, artifacts, variables, automations, cooperative cancel; keep deliberate items labeled | Matrix matches this canvas; no new parity claims | docs + shim |
| **P0.3** | Refresh `docs/ui_prefect_parity_checklist.md` against current `frontend/` routes (check done items; leave true gaps) | Checklist matches UI | frontend docs |
| **P0.4** | Strengthen `docs/how-to/port-from-prefect.md` with a “common Prefect APIs → IronFlow status” table | Port guide answers top 15 Prefect imports | docs |

### P1 — Finish in-flight correctness (1–2 sessions)

| ID | Action | Acceptance | Ownership |
| --- | --- | --- | --- |
| **P1.1** | Land / finish **task resume on retry** (Goal A) from PR [#50](https://github.com/PPPSDavid/rust-based-prefect/pull/50) / `docs/plans/task-result-cache.md` | Cancel→retry skips completed tasks; tests + COMPATIBILITY + how-to | shim + engine (+ UI) |
| **P1.2** | Resume hardening: `map` index in keys, Rust lookup hot path, clear UI “skipped vs re-run” | Cases in plan §3 covered; lite perf gate green | engine + shim + frontend |

### P2 — Self-hosted production follow-ups (already planned)

| ID | Action | Acceptance | Ownership |
| --- | --- | --- | --- |
| **P2.1** | Postgres: move schedule tick / gate / remaining CRUD off Python fallback into Rust | Compose path doesn’t require Python fallback for schedules/gates | engine + shim |
| **P2.2** | Alembic-style `ironflow server database upgrade` (or equivalent) | Documented upgrade path for Postgres | shim + docs |
| **P2.3** | HA multi-services leader election (advisory lock) | Two services processes don’t double-tick | engine + shim |
| **P2.4** | Optional: UI image + GHCR publish automation | Documented; CI publishes | infra |

### P3 — Runtime DX core (after P0–P1; do before P2)

These are **authoring/runtime fundamentals**, not polish. Prefects’ get-started path assumes you can log, observe cancel, and know which run you are in. IronFlow already has storage/API for logs and cancel *intent*; the gap is the **public, portable API + docs**.

| ID | Action | Acceptance | Ownership |
| --- | --- | --- | --- |
| **P3.0** | Stable **runtime context** helpers (`flow_run_id`, `task_run_id`, deployment id/name, parameters) — foundation for logging/cancel | ✅ `get_run_context` / `RunContext` exported + tests | shim |
| **P3.1** | Prefect-shaped **logging**: `get_run_logger()` (+ optional `log_prints=` on `@flow`/`@task`) → existing log rows / UI Logs tab | ✅ `get_run_logger` → log store/UI; `log_prints=` still open | shim (+ tiny UI if needed) |
| **P3.2** | **Lifecycle control (expanded):** (1) **Cancel terminates** running tasks (killable workers, not poll-only); (2) **Pause `drain`** — pause scheduling, let in-flight finish; (3) **Pause `terminate`** — hard brake, kill in-flight, resume retries interrupted. Modes explicit in API/UI. Plan: [`flow-run-lifecycle-control.md`](flow-run-lifecycle-control.md) | ✅ P3.2a–e + how-to (**P3.2f**); Playwright lifecycle chooser | shim + engine + frontend |
| **P3.3** | Concept pages: Deployments, Schedules, Work pools, Concurrency, Server (thin, link to how-tos) | Nav Concepts mirrors Prefect IA for supported subset | docs |
| **P3.4** | Retries / timeouts **authoring clarity**: document exact `@task`/`@flow` knobs that exist today; add missing thin knobs only if code already half-supports them | Port guide + concepts state truth; no silent Prefect-looking kwargs | shim + docs |
| **P3.5** | Lifecycle **E2E smoke**: soft pause, hard pause+resume, cancel-terminate + logs visible | Scripts/tests for all three paths | shim (+ frontend optional) |

### P4 — Concurrency ops surface (core control-plane productization)

Kernel GCL/tag/rate_limit already shipped (Phases 1–3). P4 is the **operator surface** so limits are usable outside ad-hoc Python — treat as core after P3, not optional chrome. Aligns with `docs/plans/concurrency-limits.md` Phase 4.

| ID | Action | Acceptance | Ownership |
| --- | --- | --- | --- |
| **P4.0** | GCL **correctness under cancel/resume**: leases always released on task/flow cancel; document interaction with P1 resume | ✅ cancel/terminate-pause release-by-holder; `pytest -m airtight` | shim + engine |
| **P4.1** | Async `concurrency` / `rate_limit` helpers (thin over same Rust acquire) | Async tests mirror sync semantics; no second ledger | shim |
| **P4.2** | CLI `ironflow gcl` subset (`ls` / `create` / `update` / `delete` / `inspect`) | CLI + how-to; HTTP or local plane path documented | shim |
| **P4.3** | UI concurrency admin page (list/create/edit/delete; show active leases if cheap) | Nav entry; uses existing `/api/concurrency-limits` | frontend |
| **P4.4** | `perf_matrix` **gcl/tag recipe** gate (scenario already hinted in benchmark docs) | Recipe in lite or dedicated preset; compare key stable | benchmarks |
| **P4.5** | RRule: `COUNT` and/or one advanced calendar filter (**only if a real workload needs it**) | COMPATIBILITY subset updated | engine |

### P5 — Results / cache Goal B & background tasks

| ID | Action | Acceptance | Ownership |
| --- | --- | --- | --- |
| **P5.1** | Opt-in cross-run result cache (Goal B) — narrow JSON allowlist keys | Explicit API; default off; tests for isolation | shim + engine |
| **P5.2** | `task.delay()` / background tasks via workers | Design note first; then minimal future+queue | shim + engine |
| **P5.3** | User-facing artifacts subset (`create_markdown` or IronFlow-named equivalent) | Optional; UI render | shim + frontend |

### P6 — Configuration & light control-plane APIs

| ID | Action | Acceptance | Ownership |
| --- | --- | --- | --- |
| **P6.1** | Variables JSON store (CRUD + runtime get) | Small API + docs; no secret manager claims | shim |
| **P6.2** | ~~Runtime context helpers~~ → **moved to P3.0** (core DX) | — | — |

### P7 — Events / automations (design before code)

| ID | Action | Acceptance | Ownership |
| --- | --- | --- | --- |
| **P7.1** | Design-only: automation subset (e.g. on flow `FAILED` → trigger deployment) | Plan under `docs/plans/`; explicit non-goals | docs + architecture |
| **P7.2** | Implement smallest automation engine if P7.1 accepted | One trigger type + one action; Rust evaluation preferred | engine + shim |

### P8 — Explicit park list (review to confirm)

Do **not** open sessions unless product direction changes:

- Prefect Cloud, workspaces, SSO, RBAC, PrivateLink
- Blocks ecosystem / Secrets blocks / integration packs
- Dask/Ray task runners
- Kubernetes/Docker/push work pools
- Work-queue priority & concurrency
- Assets, SLAs, webhooks product surface
- Prefect MCP server clone
- Pixel-perfect Prefect UI clone
- Full `prefect deploy` / `prefect.yaml` recipe parity

---

## P3 deep dive — runtime DX (session briefs)

### Why this is core

Prefect’s mental model after “write a flow” is: **see logs**, **cancel a run**, **know your run ids**, **retries/timeouts behave predictably**. IronFlow already persists logs and records cancel in the control plane; missing pieces are the **Prefect-shaped authoring APIs** and honest docs. Without P3, ported flows feel blind even when resume (P1) works.

### What already exists (do not reimplement)

| Piece | Location | Gap |
| --- | --- | --- |
| Log rows + `GET /api/flow-runs/{id}/logs` | `runtime._insert_log_row`, `server.list_logs` | Mostly system/transition logs; no user logger API |
| UI Logs tab | `frontend` Run detail | Ready once user logs land |
| `FlowRunCancelled`, `assert_flow_not_cancelled`, `sleep_cancelable`, `active_flow_run_id` | `prefect_compat/cancellation.py` | **Not exported** from package root; under-documented |
| Cancel API | `POST /api/flow-runs/{id}/cancel` | State flip only; bodies ignore cancel unless they poll |
| FSM `PAUSED` | Rust allows `RUNNING↔PAUSED` | Used for **gates**; no operator pause/resume API |
| Active flow ContextVar | `decorators._ACTIVE_FLOW_RUN` | No stable public `get_run_context` |
| Thread vs process runners | `task_runners.py` | Threads are not killable; terminate needs process workers |

### Recommended session slices

#### Session **P3.0 + P3.1** (logging stack) — one PR preferred

**Goal:** Public runtime context + `get_run_logger()`.

**Smallest useful subset:**

1. `get_run_context()` → frozen dataclass/namespace: `flow_run_id`, `task_run_id | None`, `flow_name`, `task_name | None`, optional deployment fields when known.
2. `get_run_logger(name: str | None = None) -> logging.Logger` whose handler appends to the control-plane log store with `flow_run_id` / `task_run_id` / level / message / timestamp.
3. Export from `prefect_compat`; document in Concepts → Tasks/Flows + port guide.
4. Optional same PR: `@flow(log_prints=True)` / `@task(log_prints=True)` redirecting `print` → logger (nice-to-have; can split).

**Hard requirements:**

- Must not hold the control-plane lock while formatting/emitting user logs (same rule as transition hooks).
- JSONL + SQLite (and Postgres if that path inserts logs) stay consistent with existing log schema.
- Tests: logger inside `@task` appears in `list_logs`; outside a run is no-op or stderr-only (pick one, document).

**Out of scope:** Prefect logging config profiles, remote log shipping, OpenTelemetry.

#### Session train **P3.2** (lifecycle control) — **expanded; design plan required reading**

Full design: [`flow-run-lifecycle-control.md`](flow-run-lifecycle-control.md).

**Product requirements (locked from review):**

1. **Cancel must terminate running tasks** — ideally deterministic + graceful (SIGTERM then SIGKILL), **independent of what the task body is doing** (not poll-only).
2. **Pause has two explicit modes** (never a single ambiguous “pause”):
   - **`drain`** — pause the flow run / block new work; **process keeps running until current RUNNING tasks complete**.
   - **`terminate`** — slam the brake; **kill in-flight tasks**; **resume retries interrupted** task runs (COMPLETED still skipped per P1).
3. Both modes **easily configurable** and **clearly denoted** (API required `mode`, UI two-button chooser, badges on the run).

**Engineering truth to respect:** CPython threads cannot be safely killed. Terminate/cancel claims require **process-isolated task workers** + a pid registry + completion fences. Cooperative helpers remain a supplement.

**Session slices:**

| ID | Focus |
| --- | --- |
| **P3.2a** | API/compat/docs lock: `InterruptMode`, pause requires mode, cancel=terminate; UI copy |
| **P3.2b** | Drain pause + resume (scheduling hold, no kill) |
| **P3.2c** | Process worker registry + terminate for **cancel** and **hard pause** |
| **P3.2d** | Hard-pause resume ↔ P1 interrupted re-run |
| **P3.2e** | UI/CLI chooser + run badges — ✅ UI chooser + badges (CLI still open) |
| **P3.2f** | How-to + MEMORY_BANK — ✅ `docs/how-to/cancel-pause-resume.md` |

**Out of scope:** Prefect human-input approval pauses; guaranteeing `finally` after SIGKILL.

#### Session **P3.3 + P3.4** (docs concepts + retries/timeouts truth)

Thin docs PR; can parallelize with early P3.2a:

- Concept pages (Deployments / Schedules / Work pools / Concurrency / Server / **Lifecycle**).
- Retries/timeouts kwargs truth table.

#### Session **P3.5** (smoke)

After P3.1 + P3.2b/c/d: soft pause, hard pause+resume, cancel-terminate; logs visible; no GCL leak.

### P3 dependencies / ordering

```text
P0 (matrix honesty)
  └─ P1.1 resume Goal A  (needed for P3.2d interrupted re-run)
       └─ P3.0 + P3.1 logging/context
            └─ P3.2a API/mode lock
                 ├─ P3.2b drain pause/resume
                 └─ P3.2c terminate workers (cancel + hard pause)
                      └─ P3.2d hard-pause resume + P1
                           ├─ P3.2e UI/CLI
                           └─ P3.5 smoke
P3.3/P3.4 docs parallel after P3.2a names freeze
P4.0 lease-on-cancel overlaps P3.2c
```

### Extra P3 enhancements worth considering

| Idea | Why | Suggest |
| --- | --- | --- |
| Task-run scoped logs in UI filter | Prefect UX; API already has `task_run_id` query param | Fold into P3.1 if cheap |
| `logger.bind`-style extras (`task_name`) | Debuggability | Optional |
| Warn on unknown Prefect kwargs | Porting footguns | P3.4 |
| Default task isolation → process when strong cancel required | Makes terminate real by default | P3.2c decision |
| Distinguish gate `PAUSED` vs operator pause in UI | Avoid wrong Resume button | P3.2e |
| Optional later: `cancel(mode=drain)` wait-then-cancel | Rare; do not default | Park unless requested |

---

## P4 deep dive — concurrency productization (session briefs)

### Why this is core (not “UI nicety”)

Global/tag limits are already a **compatibility headline** in `COMPATIBILITY.md` and the how-to. Without CLI/UI/async/perf gates, the feature is “library-only” and hard to operate in the self-hosted Compose world you just shipped. Phase 4 in `docs/plans/concurrency-limits.md` already named this work.

### What already exists

| Piece | Status |
| --- | --- |
| Rust slot ledger + leases + decay | Shipped |
| Sync `concurrency` / `rate_limit` | Shipped |
| HTTP `/api/concurrency-limits` CRUD | Shipped |
| Tag limits on enter `Running` | Shipped |
| CLI `ironflow gcl` | Missing |
| UI admin | Missing |
| Async CM | Missing |
| Dedicated perf_matrix gcl recipe | Partial / missing as gate |

### Recommended session slices

#### Session **P4.0** (correctness) — do first in the P4 train

- Cancel / fail / timeout paths release leases (including process-pool workers).
- Resume (P1) must not double-hold or orphan leases.
- Tests under contention + cancel. This protects P3.2 and P1 from silent slot exhaustion.

#### Session **P4.2** then **P4.3** (ops surfaces)

Order rationale: CLI can reuse the same client helpers the UI will call; ship CLI+docs first, UI second.

- **P4.2 CLI:** `ironflow gcl ls|create|update|delete|inspect` against API URL + auth env (Compose-friendly).
- **P4.3 UI:** simple admin page under AppShell nav; no Prefect pixel clone; show name, limit, active, mode/decay.

#### Session **P4.1** (async helpers)

- `async with concurrency(...)` / `await rate_limit(...)` as thin wrappers; same Rust ops; no asyncio lock substituting for the ledger.
- Only after sync semantics + P4.0 are trusted.

#### Session **P4.4** (perf gate)

- Add/stabilize gcl or tag-limited recipe; wire into CI or documented agent gate (`--preset` / recipes fingerprint).
- Prevents “concurrency polish” from regressing claim/acquire latency.

#### **P4.5** RRule — keep **demand-gated**

Do not schedule unless a deployment workload needs `COUNT` / calendar filters; not required for concurrency core.

### P4 dependencies / ordering

```text
P1.1 + P3.2 (cancel paths understood)
  └─ P4.0 lease-on-cancel/resume
       ├─ P4.2 CLI
       │    └─ P4.3 UI admin
       ├─ P4.1 async CM
       └─ P4.4 perf_matrix gcl recipe
P4.5 RRule — optional, separate
```

### Extra P4 enhancements worth considering

| Idea | Why | Suggest |
| --- | --- | --- |
| Show **holder count / leases** on inspect + UI | Operators debug “who holds the slot” | P4.2/P4.3 if API exposes cheaply |
| `ironflow gcl reset-leases` dangerous escape hatch | Ops recovery | Defer; document reclaim TTL instead |
| Tag limit quick-create in UI from task tags list | Nice; not needed for v1 admin | Later |
| Deployment concurrency vs GCL explainer callout | Users confuse the two | Docs in P3.3 concurrency concept page |
| Postgres GCL path parity check | Compose uses Postgres | Audit in P4.0; file P2.x if Rust op missing |

---

## Suggested session queue (copy into task briefs)

**Now (agents in flight / next):**

1. **P0.1 + P0.2** — Docs sitemap/nav + matrix honesty  
2. **P1.1** — Merge/finish task resume Goal A (#50)  
3. **P1.2** — Resume hardening + UI skipped-state  

**Core DX train (after P0–P1):**

4. **P3.0 + P3.1** — Runtime context + `get_run_logger` (+ optional `log_prints`)  
5. **P3.2a → P3.2f** — Lifecycle control: force cancel + pause `drain`/`terminate` + resume ([plan](flow-run-lifecycle-control.md))  
6. **P4.0** — GCL leases correct under cancel/pause/resume  
7. **P4.2** — CLI `ironflow gcl`  
8. **P4.3** — UI concurrency admin  
9. **P4.1** — Async concurrency helpers  
10. **P4.4** — perf_matrix gcl/tag recipe  

**Parallel / light docs:**

11. **P0.3 + P0.4 + P3.3 + P3.4** — UI checklist, port guide, concept pages, retries/timeouts truth  
12. **P3.5** — Logging + lifecycle smoke (drain / terminate / cancel)  

**After core DX:**

13. **P2.1** — Postgres Rust schedule/gate (and other Tier B follow-ups)  
14. **P5.1** or **P7.1** — Cache Goal B vs automations design  

---

## Review checklist for you

When reviewing this canvas, please mark each backlog ID:

- **Approve** — schedule a dedicated session  
- **Defer** — keep listed but later  
- **Reject / park** — move to P8 with rationale  
- **Rewrite subset** — note the smaller slice you want  

Reply with the ordered list of IDs to execute next; each follow-up session should cite this file and the chosen ID.
