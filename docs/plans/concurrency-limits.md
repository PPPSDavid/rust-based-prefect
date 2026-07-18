# Plan: Global & Tag-Based Concurrency Limits

**Status:** Implemented (Phases 1–3 MVP subset) — see `COMPATIBILITY.md` and `docs/how-to/concurrency-limits.md`.  
**Branch intent:** Document Prefect gaps and a Rust-first implementation path; Phases 1–3 landed.  
**Ownership:** `rust-engine/` (slot ledger + acquire/release), `python-shim/` (API + `concurrency`/`rate_limit` + `@task(tags=...)`), optional later `frontend/` (admin UI).  
**Forbidden areas this plan does not claim:** work-queue priority/concurrency, Cloud tenant isolation, Redis lease backends (see self-hosted Docker B4), full Prefect CLI `prefect gcl` parity, async CM.

**Upstream references (Prefect 3.x):**

- [Global concurrency limits](https://docs.prefect.io/v3/concepts/global-concurrency-limits)
- [Tag-based concurrency limits](https://docs.prefect.io/v3/concepts/tag-based-concurrency-limits)
- [How to apply global concurrency and rate limits](https://docs.prefect.io/v3/how-to-guides/workflows/global-concurrency-limits)

---

## 1. Compatibility review (2026-07-14)

### Sources checked

- Prefect docs above (tag limits: as of 3.4.19 backed by global limits named `tag:{tag_name}`)
- Local: `COMPATIBILITY.md`, `docs/PREFECT_IRONFLOW_MAPPING.md`, `docs/architecture.md`, `docs/benchmark_baseline.md`
- Code: `python-shim/src/prefect_compat/decorators.py` (`TaskWrapper.submit` / `map`), `runtime.py` (deployments only), `rust-engine/src/deployment_ops.rs` (deployment concurrency + leases), public `__init__.py` exports

### Gap table

| Prefect surface | IronFlow today | Classification |
| --- | --- | --- |
| Named **global concurrency limits** (slots, CRUD, active flag) | None — no table, API, or SDK | **missing** |
| `concurrency(...)` / `rate_limit(...)` context (sync/async, `occupy`, `strict`, leases) | None | **missing** |
| **Slot decay** / rate-limit mode | None | **missing** |
| **Tag-based** task limits (`@task(tags=...)`, check on enter `Running`) | No `tags=` on `@task`; `COMPATIBILITY.md` incorrectly claimed support | **missing** (matrix to be corrected) |
| Limit = 0 aborts tag-tagged runs (vs delay) | N/A | **missing** |
| Multi-tag **AND** (all tags must have slots) | N/A | **missing** |
| Tag wait / retry (~30s client backoff via `PREFECT_TASK_RUN_TAG_CONCURRENCY_SLOT_WAIT_SECONDS`) | N/A | **missing** |
| Deployment flow-run concurrency + collision strategy | **Supported** (`concurrency_limit`, `ENQUEUE` / `CANCEL_NEW`) in Rust claim path + Python fallback | **supported** (deployment-scoped only) |
| Work pool / work queue concurrency | Explicit non-goal in UI parity checklist | **out of scope** (near term) |

### Current IronFlow support (accurate)

- **Supported:** per-deployment concurrent run caps with deterministic claim gating in SQLite (`deployment_ops` / runtime fallback).
- **Partial:** architecture docs mention “concurrency-limit intent”; benchmarks list global/tag limits as a workload scenario but no recipe enforces them.
- **Missing:** global named slots, rate limits, task tags, slot leases for user code.
- **Stale claim:** `COMPATIBILITY.md` listed “concurrency limit tags (control-plane enforced)” — **no implementation or tests**. Corrected in the same change as this plan.

---

## 2. What Prefect actually requires (behavior summary)

### Global concurrency limits

1. Create a named limit with `limit` (max slots), optional `slot_decay_per_second`, `active`.
2. Code calls `with concurrency("name", occupy=1): ...` (or async).
3. Server **atomically** grants slots or blocks/retries until available (or timeout).
4. Slots are held for the duration (**lease** + renewal for long holds) then released.
5. `rate_limit("name")` acquires slots that **decay over time** instead of held-for-duration (requires decay configured).
6. Missing limit: warn + continue by default; `strict=True` raises.
7. Usable **outside** flows as well as inside tasks.

### Tag-based concurrency limits

1. Tasks carry `tags`; limits configured per tag (Prefect stores them as global limits `tag:{name}`).
2. Checked when a task run tries to enter **`Running`**.
3. If any tagged limit has no slot → delay transition; client retries after a wait interval.
4. Multiple tags on one task → **all** must allow (AND).
5. Untagged / unlimited tags → no gate. Limit `0` → abort (do not wait).

### Relation to deployment limits

Deployment/work-pool/queue limits are **orthogonal**: they gate **flow runs** in the worker claim path; tags/global GCLs gate **operations / task runs** (and arbitrary Python). IronFlow already has the former; this plan targets the latter under one shared **slot ledger**.

---

## 3. IronFlow-shaped design (recommended)

### Decision protocol consensus

Three lenses (control-plane correctness, Prefect ergonomics, performance) converge on:

1. **One Rust-owned slot ledger** for both global and tag limits (tag = namespaced global limit `tag:{name}`), matching Prefect 3.4.19+ and avoiding two competing counters.
2. **Lease + expire** semantics (reuse patterns from `deployment_runs.lease_until` / reclaim), not sticky in-memory counters — required for crash safety and multi-process workers later.
3. **Do not** invent a second Python-only semaphore path; `map` already races across threads, so enforce in the control plane under SQLite transactions.

### Data model (SQLite, schema upgrade)

```text
concurrency_limits (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,          -- user name or "tag:db"
  limit_slots INTEGER NOT NULL,      -- 0 = deny / abort for tag path
  active_slots INTEGER NOT NULL DEFAULT 0, -- denormalized; validated by leases
  slot_decay_per_second REAL,        -- NULL => hold-until-release only
  active INTEGER NOT NULL DEFAULT 1, -- disable without delete
  created_at TEXT, updated_at TEXT
)

concurrency_leases (
  id TEXT PRIMARY KEY,
  limit_id TEXT NOT NULL REFERENCES concurrency_limits(id),
  occupy INTEGER NOT NULL,
  holder_type TEXT,                  -- task_run | flow_run | external | none
  holder_id TEXT,
  acquired_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,          -- renewed by heartbeat or CM renewal
  mode TEXT NOT NULL                 -- concurrency | rate_limit
)
```

Notes:

- Prefer **lease rows as source of truth** for occupied slots; `active_slots` is a cache updated in the same txn (or computed via `SUM(occupy)` of non-expired leases) to keep acquire O(1) under contention.
- Rate-limit mode: granting a lease that expires/`decays` by `occupy / slot_decay_per_second` without an explicit release (or auto-release on acquire success for fire-and-forget `rate_limit`).

### Rust API surface (`rust-engine`, new module e.g. `concurrency_ops.rs`)

| Op | Semantics |
| --- | --- |
| `gcl_upsert` / `gcl_delete` / `gcl_get` / `gcl_list` | Admin CRUD |
| `gcl_acquire` | Atomic multi-name acquire: lock limits in **sorted name order**, check `active`, capacity, insert leases, bump slots. All-or-nothing. |
| `gcl_release` | Release by lease id(s); decrement; idempotent |
| `gcl_renew` | Extend `expires_at` for holder |
| `gcl_reclaim_expired` | Called from existing `deployment_maintenance` tick |
| `task_try_enter_running_with_tags` (later) | Optional FSM helper: gate `PENDING→RUNNING` on `tag:*` limits |

**FFI:** thin methods on the existing `bind_db` handle (same style as `deployment_claim_next`).

### Python shim

1. **`prefect_compat.concurrency`** (sync first; async later if needed):
   - `concurrency(names, occupy=1, timeout_seconds=None, strict=False, lease_duration=300)`
   - `rate_limit(names, occupy=1, ...)`
2. Control-plane wrappers calling Rust with Python retry/sleep when acquire returns “would block”.
3. **`@task(..., tags: Sequence[str] | None = None)`** — persist tags on `TaskRunRecord` / SQLite.
4. **Runtime gate:** before recording `task_running`, acquire `tag:{t}` for each tag (AND). On failure:
   - limit 0 → fail/abort task run (Prefect abort semantics)
   - otherwise block with backoff (env e.g. `IRONFLOW_TASK_TAG_SLOT_WAIT_SECONDS`, default 1–30s) then retry while flow run is still active / not cancelled.

### Critical interaction with the execution model (plan-time note)

**When this plan was written**, MVP `submit()` batched `PENDING`+`RUNNING` then ran the body **synchronously**, and `map()` + `ThreadPoolTaskRunner` marked mapped tasks `RUNNING` in `_prepare_map_task_runs` before workers executed bodies.

**Current behavior (post deferred-submit):** under `ThreadPoolTaskRunner` / `ProcessPoolTaskRunner`, independent `submit()` calls create a PENDING task run and return futures immediately; workers then `wait_for` → acquire tag slots → RUNNING → body. Sequential submit stays sync (untagged may still batch PENDING+RUNNING). See `COMPATIBILITY.md` and **[Tasks](../concepts/tasks.md)**.

Implications (original analysis):

| Path | Gap if we only add a Python `concurrency()` CM |
| --- | --- |
| Explicit `with concurrency("db"):` inside task body | Works once slot ledger exists; slots held for body duration |
| Tag limits on enter `Running` | **Broken** if map path keeps “all map items → RUNNING” up front — tags would look fully used before any work starts |
| Pure sequential `submit` chains | Tag limits rarely contended (one RUNNING at a time per flow) |

**Required fix for tag parity:** split start transitions:

1. Create task run → `PENDING` only.
2. `acquire` tag slots (or global CM) → then `RUNNING`.
3. On terminal state / cancel → `release` (or lease expiry).

For `map` + thread/process pools: acquire **per worker** immediately before body (not in `_prepare_map_task_runs`). That preserves fan-out parallelism under the slot cap.

---

## 4. Phased delivery

### Phase 0 — Docs honesty (this PR)

- Correct `COMPATIBILITY.md` / mapping / review backlog.
- Land this plan; no runtime behavior change.

### Phase 1 — Global slot ledger (MVP subset)

**Goals:** named limits, occupy/release, strict/missing-limit behavior, lease reclaim.

- Rust schema + `gcl_*` ops + tests (contention, idempotent release, multi-name atomicity).
- Python CRUD via control plane + minimal HTTP (`/api/concurrency-limits` subset).
- Sync `concurrency` context manager exported from `prefect_compat`.
- Shim tests: two threads / map workers cannot exceed limit.

**Non-goals in Phase 1:** slot decay, async CM, UI, tag decorator.

### Phase 2 — Tag-based limits

- `@task(tags=...)`; auto upsert/use `tag:{name}` limits (admin API or `create_concurrency_limit(tag=...)`).
- Change submit/map start path to PENDING-then-acquire-then-RUNNING.
- Cancel / fail / complete must release; process-pool path must pass tags + release on exit.
- Limit `0` abort tests; multi-tag AND tests.

### Phase 3 — Rate limits (`slot_decay_per_second`)

- `rate_limit()` acquisition mode.
- Decay math in Rust (deterministic clock source for tests).
- Document that decay is required for rate_limit (match Prefect).

### Phase 4 — API/CLI/UI polish (optional)

- CLI `ironflow gcl …` subset.
- Frontend concurrency admin page (after API stable).
- Wire into `perf_matrix` a `gcl` / tag-limited recipe (fulfill `docs/benchmark_baseline.md` scenario 4).

---

## 5. Reliability & performance requirements

### Reliability

1. **Atomic multi-limit acquire** in one SQLite txn; lock order = sorted names (deadlock-free).
2. **Idempotent release** by lease id.
3. **Crash safety:** expired leases reclaimed on maintenance tick; holders that die lose slots within `lease_duration`.
4. **Determinism:** same acquire requests under the same lease clock ordering → same grant/deny (tests with injected clock where practical).
5. **Cancel interaction:** flow/task cancel must release leases even if user code does not exit the CM cleanly (best-effort `try/finally` + reclaim).
6. **Process pool:** child processes cannot share the parent’s RLock; all slot ops go through SQLite / Rust, not in-process counters.

### Performance

1. Hot path is **Rust + SQLite**, not Python scanning “all RUNNING task runs with tag X”.
2. Index `concurrency_limits(name)`, `concurrency_leases(expires_at)`, `concurrency_leases(limit_id)`.
3. Avoid holding the global Python `_lock` across acquire wait loops — poll/retry outside, short critical sections (same spirit as transition hooks: don’t block the control-plane lock).
4. Batch reclaim in maintenance (mirror `reclaim_expired_claims`).
5. Add focused recipe later; gate regressions with `perf_matrix` lite + a future `concurrency_slots` preset when Phase 1 lands.

### Failure modes to document

| Case | Behavior |
| --- | --- |
| Limit missing, `strict=False` | Log warning; proceed (Prefect-like) |
| Limit missing, `strict=True` | Raise |
| Limit inactive | Treat as missing / no-op (match Prefect “active” flag) |
| Acquire timeout | `TimeoutError` |
| Tag limit 0 | Abort task run (do not wait) |
| Dual path Python fallback without Rust | Same schema via Python SQL; must stay feature-compatible for tests without `.so` |

---

## 6. Acceptance criteria (per phase)

### Phase 1

- [ ] Create limit `database` with `limit=2`; 4 overlapping `concurrency("database")` holds → at most 2 enter critical section.
- [ ] Multi-name acquire either grants both or neither.
- [ ] Kill holder without release → reclaim frees slots within lease TTL + tick.
- [ ] `COMPATIBILITY.md` lists exact supported subset; tests under `python-shim/tests/test_concurrency_limits.py` + Rust unit tests.
- [ ] Lite `perf_matrix` still passes after control-plane wiring.

### Phase 2

- [ ] `@task(tags=["db"])` with limit 1: thread-pool `map` of 4 never shows >1 tagged task in `RUNNING` at once.
- [ ] Two tags with AND semantics enforced.
- [ ] Limit 0 aborts; unlimited tags never block.

### Phase 3

- [ ] `rate_limit` with decay paces N starts within expected windows (± tolerance).
- [ ] `rate_limit` without decay fails clearly.

---

## 7. Likely files

| Area | Files |
| --- | --- |
| Rust | `rust-engine/src/concurrency_ops.rs` (new), `ffi.rs`, `deployment_ops.rs` (maintenance hook), schema helpers |
| Shim | `prefect_compat/concurrency.py` (new), `runtime.py`, `decorators.py`, `server.py`, `__init__.py` |
| Tests | `python-shim/tests/test_concurrency_limits.py`, Rust tests in `concurrency_ops` |
| Docs | `COMPATIBILITY.md`, `docs/PREFECT_IRONFLOW_MAPPING.md`, `docs/concepts/tasks.md`, this plan |
| Benchmarks (later) | `benchmarks/perf_matrix.py` recipe + `docs/perf_methodology.md` |

---

## 8. Recommendation

**Implement Phase 1 next** (global named slots + sync CM + leases in Rust). It unblocks the most general Prefect API (`concurrency` / shared resources) and becomes the substrate for tag limits (`tag:{name}`) without a second subsystem.

**Do not** re-claim tag-based limits in `COMPATIBILITY.md` until Phase 2 tests exist.

Deployment concurrency remains separate and already supported; document the distinction so users do not confuse “max parallel deployment runs” with “global slot named `database`”.
