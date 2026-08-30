# Task auto-retry spec (future)

**Status:** design only — `@task(retries=N)` is **not implemented**. This document prescribes semantics so future work does not reintroduce Prefect’s attempt-count ambiguity.

Related: **[Execution contract](../concepts/execution-contract.md)**, **[State transition matrix](../concepts/state-transition-matrix.md)**, **[Task resume and persist](../how-to/task-resume-and-persist.md)**.

## Problem (Prefect 3.x)

Prefect mixes two retry mechanisms with incompatible identity:

| Retry kind | `flow_run_id` | `task_run_id` | Observability |
| --- | --- | --- | --- |
| Flow retry | Same (`run_count++`) | New rows | Hard to correlate task attempts |
| Task retry | Same | Same row | `task_run.run_count` on one row |

IronFlow avoids this by using **new execution rows** for flow retries and **lineage + logical slots** for skip semantics.

## IronFlow orthogonality rules (normative)

1. **Flow retry** → always **new** `flow_run_id` + **new** `task_run_id` rows; correlate via `resume_lineage_id` + `(planned_node_id, map_index, input_fingerprint)`.
2. **In-run task retry** (future) → **same** `task_run_id`; increment **`task_run_attempt`**; append events with `transition_kind=task_retry_scheduled|task_retry_running`.
3. **Never** create a new `task_run_id` for in-run retry **and** never reuse a `task_run_id` across flow retries.

## Proposed API

```python
@task(retries=2, retry_delay_seconds=1.0)
def flaky() -> int:
    ...
```

Behavior:

- On failure inside the **same** flow run, schedule retry after delay without new task run row.
- `task_run_attempt` increments before each body execution (first run = 1).
- Terminal `FAILED` only after retries exhausted.
- Task retry does **not** interact with resume skip cache (in-run only).

## FSM extensions

| Event | Edge | Notes |
| --- | --- | --- |
| `task_retry_scheduled` | `FAILED→PENDING` or hold in `RUNNING` | TBD — prefer minimal FSM churn |
| `task_retry_running` | `PENDING→RUNNING` | Same row |
| `task_completed` | `RUNNING→COMPLETED` | Success ends retry loop |

Rust `validate_transition` may need explicit edges or task-retry as a sub-state recorded in event log only (expert review before implementation).

## Non-goals

- Prefect `retry_delay_seconds` jitter parity
- `retry_condition_fn` / `retries` per exception type (initial slice: count only)
- Cross-flow-run task retry (that is flow retry + resume skips)

## Implementation checklist (when scheduled)

1. Decorator kwargs + validation
2. `task_run_attempt` increment path in `runtime.py`
3. Delay scheduling (in-process timer vs worker tick)
4. Tests: in-run retry reuses row; flow retry still creates new rows
5. Update `COMPATIBILITY.md` and `PREFECT_IRONFLOW_MAPPING.md`

## Column readiness

`task_run_attempt` column and API field exist today (default `1`) so flow-retry work and future task-retry work can land independently.
