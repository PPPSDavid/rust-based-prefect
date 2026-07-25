# Task result cache & deterministic resume (IronFlow)

**Status:** Phase 1 implemented (resume + JSON allowlist + UI); Goal B / map hardening / Rust hot path still open  
**Last updated:** 2026-07-14  
**Scope (when implemented):** `python-shim/` (authoring + result bridge), `rust-engine/` (lookup/persist hot path), `COMPATIBILITY.md`, tests; UI later  
**User-facing docs:** [How to resume tasks and persist results](../how-to/task-resume-and-persist.md) · [Tasks](../concepts/tasks.md) · [Compatibility matrix](../../COMPATIBILITY.md)  
**Related:** [MEMORY_BANK cancel/retry](../MEMORY_BANK.md), Prefect [Caching](https://docs.prefect.io/v3/concepts/caching), compatibility backlog “Minimal task caching”

This plan separates **what IronFlow needs** from **Prefect’s full cache-policy matrix**, walks concrete cases, then recommends a small phased subset.

**Quick answers (flushed out in §5.1–§5.3):**

| Question | v1 answer |
| --- | --- |
| Does the DAG approach **automatically** cache? | **Partially.** On a *resume* run only: `None` results auto-skip; non-`None` skip only if the task opted into result persistence. Fresh runs never auto-hit. |
| Limit result types when cache/persist is enabled? | **Yes.** JSON-safe scalars/containers only, with a size cap. No pickle, DataFrames, files, or arbitrary objects in v1. |

---

## 1. Problem statement

Today:

- Task return values live only in-process on `TaskFuture.value`.
- Completion writes an artifact row with event metadata (`task_name`), **not** the return value.
- Deployment **retry** creates a **new** deployment run → **new** flow run and re-executes every task (`retry_flow_run` → `trigger_deployment_run`).
- There is no task-result store, no cache key API, and no resume lineage between retries.

Two different user goals get conflated under “caching”:

| Goal | Question | Prefect analogue |
| --- | --- | --- |
| **A. Resume** | On flow-run retry after cancel/failure, skip tasks that already **COMPLETED** in the prior attempt and restore their values for downstream work. | Partial — Prefect retries/resume + caching overlap |
| **B. Result cache** | Across **independent** runs (or flows), reuse a prior result when the author opts in. | Task caching (`cache_policy`, `cache_key_fn`, persistence) |

IronFlow should solve **A** first (already called out in MEMORY_BANK). **B** can reuse the same persistence layer with a stricter, opt-in key.

---

## 2. What exists today (identity primitives)

| Primitive | Meaning today | Stable across retries? |
| --- | --- | --- |
| `task_run_id` | UUID per task **execution** | No — new UUID every run |
| `task_name` | `@task(name=...)` or `fn.__name__` | Yes for the definition, **not unique** within a flow |
| `planned_node_id` | Logical DAG slot (`n1`, `n2`, … or `dyn_{task_name}_{i}`) | Yes **within the same flow graph shape**; allocated in call order via `next_planned_node_id` |
| Flow `run_id` | UUID per flow run | No — retry creates a new flow run |
| Deployment id + parameters | Retry reuses these | Yes for deployment-backed retries |

Important quirks:

- The **same** `@task` submitted twice gets **distinct** `planned_node_id`s (see `test_repeated_task_dag.py`).
- `task.map(...)` currently assigns the **same** `planned_node_id` to every mapped child (fan-out shares the logical node). A cache/resume key **must** include map index (or input fingerprint), not only `planned_node_id`.
- Subflow/gate tasks use synthetic names (`subflow:…`, `gate:…`) and kinds — they need explicit policies (see cases).

---

## 3. Case exploration

### Case 1 — Cancel mid-flow, then retry (primary pain)

```text
A (COMPLETED, returns 42) → B (RUNNING / CANCELLED) → C (not started)
```

Retry today: new flow run; A, B, C all run again.

**Desired:** A skipped; B and C run; C sees `42` from A’s restored result.

**Identity that works:** resume lineage (old flow run → new flow run) + `planned_node_id` (+ map index if any).  
**`task_run_id` alone does not work** — it is not shared with the new run.  
**`task_name` alone does not work** — bookend / repeated calls collide.

**None vs non-None:**

- A returns `42` → skip only helps if the value is **restored** (or recomputed). Persistence required.
- A returns `None` (side-effect “setup done”) → skip is a **completion marker**; no payload restore beyond “COMPLETED”.

This matches the user’s intuition that `None` can auto-participate, while non-`None` needs an explicit persistence/cache setup **if** we refuse to always serialize.

### Case 2 — Fresh scheduled run (new deployment tick)

Same deployment, same parameters, unrelated schedule tick.

**Desired default:** recompute everything (not resume).  
Sharing Case 1’s store without a lineage gate would incorrectly skip work.

**Rule:** resume lookup is allowed only when the new run has an explicit **`resume_from_flow_run_id`** (or equivalent lineage). Independent runs do not inherit.

### Case 3 — Same `@task` used in two different flows

```python
@task
def extract(path: str) -> dict: ...

@flow
def pipeline_a(): extract.submit("/a")

@flow
def pipeline_b(): extract.submit("/a")
```

Is this the “same” task?

| Keying choice | Behavior |
| --- | --- |
| Python function object / qualname only | Cross-flow hit for `/a` — surprising for resume; OK only for **opt-in global cache** |
| `(flow_name, planned_node_id)` | No cross-flow hit — correct for resume |
| Prefect-like `hash(inputs + task source + flow_run_id)` | Default no cross-run hit; custom policy can drop `flow_run_id` |

**Recommendation:** treat cross-flow reuse as **Goal B only**, never as default resume.

### Case 4 — Repeated submits of the same task in one flow

```python
status.submit("start")  # planned n1
work.submit(1)
status.submit("end")    # planned n2
```

Caching by `task_name="status"` would skip the second call incorrectly.  
**`planned_node_id` is the correct logical identity** for Goal A.

### Case 5 — `map` fan-out

```python
double.map([1, 2, 3])  # shared planned_node_id today
```

Resume/cache key must be at least:

`planned_node_id + map_index`  
(or `planned_node_id + stable_input_fingerprint`).

Without that, skipping one mapped child would skip all, or restore the wrong value.

### Case 6 — Inputs changed on retry

User retries with **different** deployment parameters after a partial run.

- Strict resume-by-node: still skips A even if inputs changed → **wrong**.
- Safer v1: resume only when `requested_parameters` equal the prior attempt (byte-identical JSON), else full recompute.
- Goal B later: input-aware cache keys.

### Case 7 — Non-`None` result, no cache setup (user rule 3)

On retry, recompute. That means:

- Downstream that depended on the old value must either wait for recompute or see a new value.
- For Goal A “skip expensive completed work,” authors must opt into persistence for value-producing tasks.

**Tension with MEMORY_BANK:** “already-completed tasks should not be recomputed” conflicts with rule 3 unless we interpret MEMORY_BANK as applying only to opted-in / `None` tasks, or we always persist results for resume (simpler UX, more storage).

See §5 for the consensus resolution.

### Case 8 — `None` result, auto behavior (user rule 2)

Auto-skip on **resume** is safe and cheap (store only `{completed: true}`).

Auto-skip on **unrelated runs** would be dangerous for side-effect tasks (`send_email` → `None`): a later flow run would skip the email.  
**Auto-`None` applies only inside resume lineage**, not as a global cache.

### Case 9 — Failed / cancelled tasks

Never treat as cache hits. Only `COMPLETED` entries are eligible.  
A task that failed then succeeded on a later attempt in the **same** lineage: last successful completion wins.

### Case 10 — Subflows and gates

| Kind | Resume behavior (proposed) |
| --- | --- |
| Normal task | Subject to rules in §5 |
| `kind=gate` | Always re-evaluate `open_at` / wait (time-based; caching is wrong) |
| Inline subflow | Resume applies to **child** task graph via child flow-run lineage |
| `kind=subflow` (deployment-backed) | Parent surrogate task completes when child flow completes; resume may skip re-enqueue if child flow run COMPLETED and parameters match — **phase 2+** |

### Case 11 — Unserializable / huge results

Opt-in persistence for non-`None` must define a serialization boundary (JSON-able subset first; pickle later or never).  
If persist fails, mark task completed **without** a reusable payload → on resume, **recompute** (fail open toward correctness).

### Case 12 — Concurrent retries / duplicate workers

Two workers claiming retry lineage must not double-write corrupt entries.  
Store updates should be idempotent by `(lineage_id, logical_task_key)` with last-writer or first-COMPLETED-wins. Prefer Rust + SQLite for the hot path.

---

## 4. Prefect vs proposed IronFlow (delta)

| Prefect default | Proposed IronFlow v1 |
| --- | --- |
| Cache key from inputs + task source + flow run id | **Resume key** from lineage + `planned_node_id` (+ map index) |
| Requires result persistence setting | Persist **completion marker** always for resume eligibility; persist **payload** only when opted in (or always for small JSON — see §5) |
| Rich `cache_policy` algebra | No policies in v1 |
| Cross-task / cross-flow key sharing | Out of scope for v1 |
| `Cached` state | Optional later; v1 can complete with `data.cache_hit=true` / artifact flag |

IronFlow intentionally does **not** claim Prefect cache parity in v1.

---

## 5. Design consensus (three-expert protocol)

**Question:** For IronFlow v1, should non-`None` results always persist for resume, or only when the author opts in?

1. **Compat expert:** MEMORY_BANK and cancel→retry UX want “don’t recompute completed work.” Always persisting JSON-serializable results for resume matches that; opt-in-only leaves a footgun where expensive tasks re-run unless decorated.
2. **Simplicity expert:** User rule 3 (non-`None` without setup recomputes) is clear and avoids surprising serialization. Prefer explicit `@task(persist_result=True)` / `@task(cache=True)` for values; auto-marker for `None`.
3. **Perf expert:** Always serializing large payloads on every completion hurts the control-plane path. Markers are cheap; payloads should be opt-in or size-capped.

**Compat drops “always persist everything”** after perf/size concerns.  
**Simplicity and perf agree** on: markers always (for `None` and for “completed” bookkeeping); **payload persistence opt-in** for non-`None`.

**Refined consensus for v1:**

1. **Goal A (resume) only** — no cross-flow / cross-run cache by default.
2. **Logical key:** `(resume_lineage_id, planned_node_id, map_index | None)`.
3. **Eligibility:**
   - Task `COMPLETED` with return value `None` → **auto-eligible** (marker only).
   - Task `COMPLETED` with non-`None` → eligible **only if** `@task(persist_result=True)` (name TBD) and payload serializes.
   - Otherwise → recompute on resume.
4. **Lineage:** retry API records `resume_from_flow_run_id` (and copies lineage root). Lookup walks prior attempts in the chain until a hit or exhaust.
5. **Parameter guard:** if deployment parameters differ from the resumed attempt, disable resume for that run.
6. **Goal B (Prefect-like cache):** separate follow-up — opt-in key function / input hash, explicit scope (`flow` | `deployment` | `global`), never enabled by `None` auto-behavior alone.
7. **Gates:** never resume-cached. **Map:** require index in key. **Subflow resume:** phase 2.

Naming bikeshed (pick at implement time): `persist_result=True` vs `cache=True` vs `resume=True`. Prefer `persist_result` to avoid implying Prefect `cache_policy`.

---

## 5.1 DAG-based resume model (flushed out)

Think of a flow run as a **logical DAG** whose nodes are `planned_node_id`s (plus map indices). A retry is a **new flow run** that may **rebinding** onto prior node outcomes from the same lineage — not a mutation of the old run.

```text
Attempt 1 (flow_run=R1, lineage=L)          Attempt 2 (flow_run=R2, resume_from=R1, lineage=L)
─────────                                    ─────────
n1 setup        COMPLETED (None)     ──►     n1  SKIP  (marker hit)     → TaskFuture(None)
n2 expensive    COMPLETED (payload)  ──►     n2  SKIP  (payload hit)    → TaskFuture({...})
n3 work         CANCELLED            ──►     n3  RUN
n4 downstream   not started          ──►     n4  RUN   (sees restored n2 value via .result())
```

### Runtime algorithm (per `task.submit` / mapped child)

```text
1. Allocate planned_node_id (+ map_index) as today.
2. If this flow run has no resume lineage → always execute (no lookup).
3. Else lookup store[lineage, planned_node_id, map_index]:
   a. miss / FAILED / CANCELLED / no usable payload → execute
   b. hit COMPLETED + value is None → skip body; return TaskFuture(None); emit completed w/ cache_hit
   c. hit COMPLETED + payload present + task.persist_result → deserialize; return TaskFuture(value); emit cache_hit
   d. hit COMPLETED + non-None but task lacks persist_result or payload unusable → execute (recompute)
4. On successful execute:
   a. always write completion marker for this DAG key
   b. if persist_result and value is not None → try encode (see §5.3); on success store payload; on failure keep marker only
```

Downstream wiring stays ordinary Python: skipped tasks still return a `TaskFuture` with the restored (or `None`) value, so `wait_for` / `.result()` need no special API.

### What is automatic vs opt-in?

| Situation | Automatic? | Why |
| --- | --- | --- |
| Fresh run / schedule tick | **No** lookup | Avoids skipping side effects across independent runs |
| Resume run + task returned `None` | **Yes** skip | Marker-only; no serialization burden |
| Resume run + non-`None` + default `@task` | **No** skip (recompute) | Matches user rule 3; no silent pickle/JSON of arbitrary objects |
| Resume run + non-`None` + `@task(persist_result=True)` | **Yes** skip if encode ok | Author opted in; type allowlist applies |
| Gate / time-based nodes | **Never** skip | Time must re-evaluate |
| Cross-flow / cross-deployment reuse | **Never** in v1 | Goal B later |

So: the **DAG keying is always how we identify nodes**, but **automatic caching is only the `None`-marker path on resume**. Value-producing tasks are opt-in.

### Why not auto-persist every JSON-looking return?

Tempting (better cancel→retry UX), but:

- Authors returning ad-hoc objects would see silent recompute or surprising encode errors.
- Control-plane completion path would always pay encode cost.
- Harder to explain than: “`None` resumes free; values need `persist_result`.”

If product feedback later wants “auto for JSON-safe returns,” that can be a `@task(persist_result="auto")` or flow-level default without changing the DAG key.

---

## 5.2 Opt-in surface

```python
@task  # None → auto resume-skip; non-None → recompute on resume
def setup() -> None: ...

@task(persist_result=True)  # values eligible if type allowlist + size ok
def expensive(x: int) -> dict: ...

@task(persist_result=True)
def bad() -> MyClass:  # not JSON-safe → marker only; resume recomputes (warn once)
    return MyClass()
```

Semantics of `persist_result=True`:

- Declares intent to **reuse the return value on resume**.
- Does **not** enable cross-run Prefect-style caching.
- Encode failure is non-fatal to the live run (task still COMPLETED); resume simply cannot skip.

Optional later knobs (out of v1): `persist_result="auto"`, flow-level default, max payload override.

---

## 5.3 Result type allowlist (yes — limit hard in v1)

**Consensus:** limit persisted payloads to a small JSON subset to bound performance and interface surface.

### Allowed (v1)

| Type | Notes |
| --- | --- |
| `None` | Marker path (no payload blob needed) |
| `bool`, `int`, `float` | JSON numbers/bools; reject `inf`/`nan` (not JSON) |
| `str` | UTF-8; counts toward size cap |
| `list` / `dict` | Nested only with allowed types; dict keys must be `str` |
| JSON `null` round-trip | Only for explicit `None` inside containers |

### Rejected in v1 (recompute on resume)

| Type / shape | Why defer |
| --- | --- |
| `bytes` / `bytearray` | Encoding choice (base64) adds API surface |
| `datetime` / `date` / `UUID` | Need canonical encoding; easy to get wrong vs Prefect |
| `tuple` / `set` / custom sequences | JSON erases type |
| `pathlib.Path`, files, IO | Not values — side-effect territory |
| pandas / numpy / Arrow | Huge, version-sensitive |
| Pydantic / dataclasses / namedtuple | Needs schema registry |
| Arbitrary objects / pickle | Security + version fragility |

### Limits

| Limit | Proposed default | On breach |
| --- | --- | --- |
| Encoded UTF-8 size | **64 KiB** | Do not store payload; log warning; live run still succeeds |
| Nesting depth | **32** | Same |
| Container length | **10_000** elements (top-level or nested count) | Same |

Encoder: stdlib `json.dumps` with a strict default that **raises** on unknown types (no `default=` that coerces). Decoder: `json.loads` only — never `pickle` / `eval`.

### Why this bound helps

- **Perf:** encode/decode stays microseconds–low ms; fits SQLite TEXT/BLOB next to existing artifact rows.
- **Interface:** one mental model (“JSON values”); no result-storage backend API in v1.
- **Correctness:** fail open to recompute rather than restoring a wrong Python type.
- **Expansion path:** later add tagged codecs (`{"__ironflow__": "datetime", "v": "..."}`) or external blob refs without changing DAG keys.

---

## 6. Answers to open questions

### Does it automatically cache?

**Short answer:** only on **resume**, and only for:

1. `None` results (always), and  
2. non-`None` results when `persist_result=True` **and** the value passes the allowlist.

It does **not** automatically cache across fresh runs, schedule ticks, or other flows. See §5.1.

### Should we limit result types when cache/persist is enabled?

**Yes.** v1 = JSON-safe scalars/containers + size/depth caps (§5.3). Richer types are a deliberate later codec layer, not a silent pickle escape hatch.

### Performance impact?

| Path | Cost |
| --- | --- |
| Write completion marker | One SQLite row / JSONL record — similar to today’s artifact insert; acceptable on the completion hot path if done in Rust persist batch |
| Write opted-in payload | Dominated by serialization size; keep off the critical FSM lock; **64 KiB cap** fails open to “marker only” |
| Read on task start (resume run only) | One keyed lookup; skip user function + PENDING/RUNNING work; large win for slow tasks |
| Non-resume runs | No lookup (lineage absent) — **zero** overhead |
| Rejected type at complete | One failed encode attempt; no payload write |

Lite `perf_matrix` should add a recipe: multi-task flow cancel+retry with first task persisted, assert second attempt wall time drops and task event shows skip.

### How should the cache be defined? Same task across two flows?

**v1:** “Same task” means **same logical DAG slot in a resume lineage**, not the same Python callable across flows.

Across two flows: **no sharing** unless Goal B opts in with an explicit key scope later.

### What is the “same” task?

For Goal A:

```text
same := resume_lineage_id
        + planned_node_id
        + map_index? 
        + (parameters unchanged)
        + prior state == COMPLETED
        + (None marker | persist_result payload present + allowlist ok)
```

Not: `task_run_id`, not bare `task_name`, not bare Python `id(fn)`.

---

## 7. Proposed API sketch (non-normative)

```python
@task  # returns None → auto resume-skip (marker)
def setup() -> None:
    ...

@task(persist_result=True)  # JSON-safe non-None → resume restores value
def expensive(x: int) -> dict:
    return {"x": x, "n": 42}

@task  # non-None, no persist → recomputed on resume
def volatile(x: int) -> int:
    return x + 1
```

Retry (deployment-backed) becomes conceptually:

```text
trigger_deployment_run(..., resume_from_flow_run_id=old_id)
```

UI later: task run detail shows `resumed_from` / `cache_hit` (not required for v1 correctness).

---

## 8. Implementation phases (when coding starts)

| Phase | Deliverable | Validation |
| --- | --- | --- |
| **0** | This plan + COMPATIBILITY “missing” row for task resume/cache | Doc review |
| **1** | Result store schema (marker + optional JSON payload); lineage on retry; skip in `TaskWrapper.submit`; JSON allowlist encoder | Shim tests: cancel→retry Cases 1,4,6,8; reject non-JSON / oversize |
| **2** | Map index in key; parameter guard; Rust-backed lookup/persist | Map + multi-worker tests; lite perf recipe |
| **3** | Subflow/gate policies; UI `cache_hit` | Subflow tests; UI checklist |
| **4** | Goal B opt-in cross-run cache (separate design spike) | Compat matrix “partial”; do not claim Prefect parity |

Forbidden in early phases: Prefect `cache_policy` algebra, Redis lock managers, silent global sharing by task name.

---

## 9. Compatibility matrix note (draft text for implement PR)

> **Task resume / result cache (subset, planned):** On deployment-backed flow-run retry, IronFlow may skip `COMPLETED` tasks from the prior attempt in the same resume lineage when (a) the task returned `None`, or (b) `@task(persist_result=True)` and a JSON payload was stored. Non-persisted non-`None` results recompute. Not Prefect cache-policy parity; no cross-flow cache by default.

---

## 10. Decision log entry (to append when Phase 1 starts)

```md
### YYYY-MM-DD — Task resume vs Prefect-style cache
- Context: Cancel/retry recomputes completed tasks; users want simpler-than-Prefect caching.
- Decision: v1 = resume-within-lineage keyed by planned_node_id (+ map index); None auto-marker; non-None requires persist_result; no cross-flow default.
- Alternatives rejected: Prefect DEFAULT cache_policy in v1; keying by task_name only; always serializing all results.
- Consequences: Update COMPATIBILITY + MEMORY_BANK; add shim tests and a lite perf recipe before claiming the gap closed.
```
