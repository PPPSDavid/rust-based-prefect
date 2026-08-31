# States and transitions

IronFlow’s control plane uses a single **`RunState`** enum for **flow runs** and **task runs**. States and **allowed transitions** are enforced in the Rust engine (`rust-engine`); invalid transitions are rejected.

## States

| State | Meaning (high level) |
| --- | --- |
| **SCHEDULED** | Run record exists; not yet ready to execute. |
| **PENDING** | Ready to start (may be waiting on dependencies). |
| **RUNNING** | User/worker code may be executing. |
| **PAUSED** | Operator drain/terminate pause, or a temporal gate wait. |
| **COMPLETED** | Finished successfully (terminal). |
| **FAILED** | Finished with failure (terminal). |
| **CANCELLED** | Stopped or aborted (terminal). |

Terminal states (**COMPLETED**, **FAILED**, **CANCELLED**) accept **no** further transitions.

## Allowed transitions

From state **A** to **B** is allowed only when:

| From | To |
| --- | --- |
| SCHEDULED | PENDING, CANCELLED |
| PENDING | RUNNING, CANCELLED |
| RUNNING | COMPLETED, FAILED, CANCELLED, PAUSED |
| PAUSED | RUNNING, CANCELLED |

Self-transitions (same state → same state) are **invalid** at the validation layer.

The authoritative logic is **`validate_transition`** in `rust-engine/src/engine.rs` alongside the **`RunState`** definition.

## Tokens and idempotency

State updates carry a **transition token** (UUID). Re-applying the **same** token and transition is treated as **idempotent** (see engine tests: duplicate tokens are safe). This supports retries and duplicate delivery without double-applying distinct work.

## Concurrent-state invariants

`perf_matrix` measures throughput. It does **not** prove these invariants. The harness is `pytest -m airtight` (see [airtight-concurrency plan](https://github.com/PPPSDavid/rust-based-prefect/blob/main/docs/plans/airtight-concurrency.md)):

- Duplicate tokens: exactly one `applied` per token, even under a thread storm and across many flow runs.
- Parallel flow runs + concurrent `submit`: every flow ends in a legal terminal; versions are monotonic.
- `wait_all`: a contributing `FAILED` child cannot yield a `COMPLETED` flow; `detach=True` is excluded.
- Cancel/terminate vs late `COMPLETED`: a `CANCELLED` task row stays cancelled.
- Dual worker claim: exactly one `CLAIMED` winner.
- GCL: cancel or terminate-pause of a holder frees slots (`active_slots` matches live leases).

Writes stay **single-writer** per control plane. Do not shard the FSM lock to chase scale.

## Transition hooks

User **`transition_hooks`** default to **observe** mode: they see **successful** transitions **after** commit. Some start paths may emit **multiple** edges in quick succession (for example the batched `PENDING` / `RUNNING` path); see **[Compatibility matrix](../compatibility.md)** for hook ordering details.

If a hook **returns a ``RunState`` or ``TransitionDecision``**, IronFlow may **rewrite** a proposed **`RUNNING` → `{COMPLETED, FAILED, CANCELLED}`** destination **before** commit. The engine still applies **one** legal edge; `FAILED→COMPLETED` after commit is impossible because terminals are strict. Hooks that return **`None`** observe the **committed** edge (a salvage to `COMPLETED` does not also fire `to_state=FAILED` observers, unless that observer ran earlier in registration order on the proposed edge). Operator/user cancel does not apply rewrites.
