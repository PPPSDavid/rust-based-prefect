# Flow-run lifecycle control: cancel, pause, resume

**Status:** Design accepted — **P3.2a–e + P3.2f implemented** (mode-required pause, drain/resume, process-kill terminate under `ProcessPoolTaskRunner`, hard-pause resume via P1 `prepare_resume` / deployment retry, UI pause chooser + lifecycle badges, user guide `docs/how-to/cancel-pause-resume.md`). CLI pause helpers still open.  
**Canvas ID:** **P3.2** (expanded) — see `docs/plans/prefect-gap-canvas.md`  
**Last updated:** 2026-07-25  
**Depends on:** P1.1 resume lineage (interrupted-task retry on hard pause / cancel→retry); P3.0 context helpful  
**Ownership:** `rust-engine/` (FSM + signals), `python-shim/` (runners, API, killable workers), `frontend/` (clear mode UX), docs  

Related: MEMORY_BANK cancel/retry notes, gate-driven `PAUSED` (calendar wait ≠ operator pause).

---

## Implementer note — critical runtime finding

**CPython cannot safely kill arbitrary work running on another thread.**  
`ThreadPoolTaskRunner` (today’s default) can only do *cooperative* cancel (poll `assert_flow_not_cancelled` / `sleep_cancelable`). Flipping the flow run to `CANCELLED` / `PAUSED` does **not** stop a blind `time.sleep`, network call, or native extension on a worker thread.

**Implication for this plan’s product claims** (“cancel / hard-pause stops tasks regardless of what they are doing”):

| Execution model | Can honor `InterruptMode.TERMINATE`? |
| --- | --- |
| In-thread task body (`ThreadPoolTaskRunner`) | **No** — best-effort cooperative only; do not claim hard kill |
| **Process-isolated** task worker (subprocess / process pool / pid registry) | **Yes** — `terminate()` → SIGTERM → grace → SIGKILL; fence late completions |

**When implementing P3.2c (and any cancel path):**

1. Treat **process-isolated task workers + `task_run_id → pid` registry + completion generation fence** as the real terminate mechanism — not thread interrupts.
2. Do not ship UI/docs that promise “immediate stop” while tasks still run only on threads unless isolation is upgraded.
3. Keep cooperative helpers as a **supplement** (library code that opts in), never as the sole cancel story.
4. Details and phases: **§5 How terminate actually works** below.

IronFlow already has the cooperative primitives in `python-shim/src/prefect_compat/cancellation.py`; they are necessary but **not sufficient** for the terminate product requirement.

---

## 1. Product intent

Prefect supports pause/resume in ways that are easy to misuse. IronFlow should offer **two sharply named interrupt modes** and make **cancel actually stop work**, not only flip control-plane state while user code keeps sleeping.

| Operator action | Desired behavior |
| --- | --- |
| **Cancel** | Terminal stop. **Immediately** stop running task execution (deterministically, as gracefully as the isolation model allows), mark in-flight work interrupted/cancelled, release leases, do not start new tasks. Flow → `CANCELLED`. |
| **Pause (drain)** | Soft brake. Flow marked paused for scheduling; **in-flight RUNNING tasks keep running until they finish**; **no new tasks start**. When in-flight set drains → flow stays / settles `PAUSED`. Resume continues remaining work. |
| **Pause (terminate)** | Hard brake. Same as cancel for **in-flight task bodies** (kill ASAP), but flow goes **`PAUSED`** (not terminal). Resume **retries interrupted task runs** (and continues not-started work), reusing P1 resume/result rules where applicable. |

Both pause modes must be **easily configurable** and **clearly denoted** in API, CLI, UI, events, and docs (never a single ambiguous “pause”).

---

## 2. Three-expert consensus (architecture)

**Expert A — Semantics / UX:** Names must not collide with Prefect’s fuzzy pause. Prefer explicit mode enums on every API (`drain` vs `terminate`). Cancel is not “pause with different UI”; cancel is always terminal. Soft pause must never kill; hard pause must never pretend tasks are still running.

**Expert B — Runtime reality:** CPython **cannot safely kill arbitrary threads**. “Regardless of what the task is running” is only honest if task bodies run in **killable workers** (subprocess / process-pool worker / future process sandbox). Thread-pool path can stay as a documented degraded mode (`cooperative` only) or be upgraded later to subprocess-per-task.

**Expert C — Control plane:** Extend existing FSM (`RUNNING ↔ PAUSED`, `→ CANCELLED`) rather than inventing Prefect interactive input. Interrupt reason + mode must be durable on the flow run and task runs so resume/retry is deterministic. GCL leases release on any interrupt path (ties to P4.0).

**Consensus:**

1. Introduce a first-class **`InterruptMode`**: `drain` | `terminate`.
2. **Cancel** uses **`terminate`** (default; only mode for v1 cancel unless we later add `cancel(mode=drain)` as a rare “wait then cancel”).
3. **Pause** requires an explicit mode — **no default that surprises**; API/UI must choose `drain` or `terminate`.
4. Deliver **terminate** via **process-isolated task execution** as the supported kill path; keep cooperative helpers as a supplement, not the primary story.
5. **Hard-pause resume** and **cancel→retry** share P1 lineage / planned_node_id skip-or-rerun logic: COMPLETED skip (per P1 rules); interrupted/cancelled-in-flight **re-run**.

---

## 3. Naming & configuration (must be obvious)

### 3.1 Canonical names

| Name | Meaning |
| --- | --- |
| `InterruptMode.DRAIN` | Wait for current RUNNING tasks; block new starts; then hold. |
| `InterruptMode.TERMINATE` | Kill current task workers ASAP; mark those task runs interrupted. |

**API shape (proposed):**

```http
POST /api/flow-runs/{id}/cancel
  # body optional; cancel always terminate in v1
  { "mode": "terminate" }   # only accepted value initially

POST /api/flow-runs/{id}/pause
  { "mode": "drain" | "terminate" }   # required

POST /api/flow-runs/{id}/resume
  {}
```

**Python (proposed):**

```python
plane.pause_flow_run(run_id, mode="drain")       # or InterruptMode.DRAIN
plane.pause_flow_run(run_id, mode="terminate")
plane.resume_flow_run(run_id)
plane.cancel_flow_run(run_id)  # terminate semantics
```

**UI:** Pause opens a chooser — two buttons, not one:

- “Pause — let running tasks finish” → `drain`
- “Pause — stop running tasks now” → `terminate`

Cancel stays a single destructive action with copy: “Stops the run and terminates running tasks.”

**Env / flow defaults (optional later):**

- `IRONFLOW_DEFAULT_PAUSE_MODE` — **unset by default** (force explicit API/UI choice).
- `@flow(task_isolation="process"|"thread")` — process required for true terminate.

### 3.2 Denotation on records

Persist on flow run (and echo in API/UI):

- `lifecycle_action`: `cancel` | `pause` | `resume` | null  
- `interrupt_mode`: `drain` | `terminate` | null  
- `paused_at` / `cancelled_at` timestamps  
- Human summary string for UI: e.g. `Paused (terminate) — 2 tasks interrupted`

Task runs interrupted by terminate get:

- state `CANCELLED` (or keep `FAILED` with typed reason — prefer **`CANCELLED` + `interrupt_reason=terminated_by_pause|terminated_by_cancel`**)
- flag/column `interrupted=true` so resume knows to **re-run** them (unlike user-completed cancel of never-started PENDING, which stay cancelled)

---

## 4. Behavioral matrix

| Situation | drain pause | terminate pause | cancel (terminate) |
| --- | --- | --- | --- |
| Flow state target | `PAUSED` (after drain complete; may pass through “pausing” UX) | `PAUSED` ASAP | `CANCELLED` ASAP |
| New task starts | Blocked | Blocked | Blocked |
| RUNNING task bodies | Run to completion | Killed | Killed |
| RUNNING task state after | Natural terminal | `CANCELLED` + interrupted | `CANCELLED` + interrupted |
| PENDING / not started | Remain pending (held) | Remain pending (held) | `CANCELLED` |
| GCL leases | Released when task ends naturally | Released on kill path | Released on kill path |
| Resume | Continue pending work; keep completed results | Re-run interrupted; continue pending; keep completed (P1) | N/A (terminal); use **retry** API for new attempt |
| Detached tasks (`detach=True`) | Policy: do not block drain; still kill on terminate if still children of flow — **document**; prefer kill on terminate/cancel | same | same |
| Deployment-backed subflows | Propagate pause/cancel per existing child rules; terminate should cancel active children | same | same |

**Gate `PAUSED`:** Today gates may put a flow in `PAUSED` while waiting on `open_at`. Operator pause must be distinguishable (`lifecycle_action=pause` vs gate). Resume of operator pause must not confuse gate open ticks — design note: gate wait can remain a task-level wait; operator pause sets a flow-level hold flag checked before starting new tasks / before gate promotion schedules work.

---

## 5. How terminate actually works (honest engineering)

### 5.1 Non-negotiable constraint

- **`ThreadPoolTaskRunner`:** cannot forcibly stop arbitrary Python in another thread. Cooperative cancel remains best-effort only.
- **`ProcessPoolTaskRunner` / process-isolated task workers:** can `terminate()` / kill process group → closest to “regardless of what the task is doing.”

### 5.2 Target architecture (phased)

**Phase A — Policy + control plane (can land first):**

- API/CLI/UI modes, FSM transitions, durable `interrupt_mode`, block scheduling of new tasks while paused/cancelling.
- Cooperative wake-ups still used where process kill not available.
- Docs state clearly when terminate is best-effort vs guaranteed.

**Phase B — Killable task workers (required for the product claim):**

- Run task bodies in **subprocess workers** owned by the flow/worker process (even when “thread” runner is requested for map *scheduling*, execution may still be process-isolated — decision in implementation).
- Maintain a registry: `task_run_id → worker handle (pid / process)`.
- On `terminate`: send SIGTERM → brief grace → SIGKILL; mark task interrupted; release GCL; do not accept late COMPLETED from killed pid (ignore or fence with generation token).
- Determinism: same cancel/pause request → same task set interrupted (in-flight set snapshot under control-plane lock, then kill outside lock).

**Phase C — Defaults:**

- Prefer process isolation when terminate/cancel must be strong.
- Document: true immediate cancel requires process isolation; shipping a warning if cancel/terminate used under pure thread pool.

### 5.3 Gracefulness

“Graceful” here means:

1. Control-plane state updates are ordered and idempotent.  
2. Leases/resources released.  
3. SIGTERM before SIGKILL with configurable grace (`IRONFLOW_TASK_TERMINATE_GRACE_SECONDS`, default small, e.g. 5s).  
4. Not: running finally-blocks in killed threads (unreliable) — process `__exit__` / atexit best-effort only.

---

## 6. Resume semantics

| Prior action | Resume behavior |
| --- | --- |
| Pause drain | Flow `PAUSED` → `RUNNING`; start next PENDING tasks; completed tasks untouched |
| Pause terminate | In-process: `prepare_resume` for next `@flow()` invoke; prior attempt terminalized `CANCELLED` (`superseded_by_terminate_resume`). Deployment-backed: retry-with-`resume_from`. Skip COMPLETED per P1; interrupted work re-runs |
| After cancel | No resume — use deployment **retry** (P1 lineage) |

Resume must be rejected unless `lifecycle_action=pause` and state=`PAUSED` (not gate-only pause without operator action — or allow resume only when `interrupt_mode` is set).

---

## 7. FSM / engine notes

Already allowed in Rust today:

- `RUNNING → PAUSED | CANCELLED | COMPLETED | FAILED`
- `PAUSED → RUNNING | CANCELLED`

Likely additions:

- Optional intermediate UX state is **not** required in the enum if we use flags (`pause_pending_drain=true`) while still `RUNNING` until drain completes, then `PAUSED`.
- Task: `RUNNING → CANCELLED` on terminate (already allowed).
- Fence tokens so late worker heartbeats cannot overwrite interrupted terminal state.

Update `docs/concepts/states-and-transitions.md` (today’s published table omits `PAUSED`).

---

## 8. Session split (implementation)

| Session | Deliverable | Acceptance |
| --- | --- | --- |
| **P3.2a** | Design locked in COMPATIBILITY + this plan; API stubs + mode validation; UI copy wireframes/checklist | Modes required on pause; cancel documented as terminate |
| **P3.2b** | Drain pause + resume (no kill): block new starts; wait in-flight; `PAUSED`; resume continues | Tests for soft pause mid-flow |
| **P3.2c** | Terminate path for cancel + hard pause via process worker registry + SIGTERM/SIGKILL fence | Blind `time.sleep` in child process stops; thread-pool limitation documented or removed for terminate |
| **P3.2d** | Hard-pause resume re-runs interrupted tasks (P1 integration) | Interrupted tasks re-execute; completed skipped per P1 |
| **P3.2e** | UI/CLI: pause chooser, cancel copy, badges for mode | ✅ UI chooser + badges (CLI still open) |
| **P3.2f** | Docs how-to + port guide; MEMORY_BANK rewrite of cancel section | ✅ `docs/how-to/cancel-pause-resume.md` + port/mapping updates |

Cooperative helpers (`sleep_cancelable`, etc.) still exported as **supplement** for library code that opts in — never the only cancel story.

---

## 9. Explicit non-goals

- Prefect human-input / approval forms (`pause_flow_run` with wait for input UI)
- Killing sibling OS processes unrelated to the task worker registry
- Guaranteeing finally-blocks run after SIGKILL
- Soft-cancel-as-default (rejected — cancel terminates)
- Pixel Prefect pause UX

---

## 10. Test matrix (minimum)

1. Soft pause while task sleeping in process → task completes → flow `PAUSED` → resume runs downstream.  
2. Hard pause while task sleeping → process dead → task interrupted → flow `PAUSED` → resume re-runs that task.  
3. Cancel while two tasks running → both workers dead → flow `CANCELLED` → no resume; retry uses P1.  
4. GCL slot held in cancelled/terminated task → slot free after grace/reclaim.  
5. Idempotent double pause/cancel tokens.  
6. Gate wait + operator pause interaction (no stuck run).  
7. UI/API reject pause without `mode`.

---

## 11. Docs touch list (when implementing)

- `COMPATIBILITY.md` — supported lifecycle subset  
- `docs/concepts/states-and-transitions.md` — PAUSED + interrupt modes  
- `docs/how-to/cancel-pause-resume.md` (new)  
- `docs/how-to/port-from-prefect.md` — map Prefect pause/cancel  
- `docs/MEMORY_BANK.md` — replace “cooperative only” story  
- `docs/ui_prefect_parity_checklist.md` — pause chooser  
- Gap canvas P3.2 row — point here  
