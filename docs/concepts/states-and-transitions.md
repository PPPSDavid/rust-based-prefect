# States and transitions

IronFlow’s control plane uses a single **`RunState`** enum for **flow runs** and **task runs**. States and **allowed transitions** are enforced in the Rust engine (`rust-engine`); invalid transitions are rejected.

## States

| State | Meaning (high level) |
| --- | --- |
| **SCHEDULED** | Run record exists; not yet ready to execute. |
| **PENDING** | Ready to start (may be waiting on dependencies). |
| **RUNNING** | User/worker code may be executing. |
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
| RUNNING | COMPLETED, FAILED, CANCELLED |

Self-transitions (same state → same state) are **invalid** at the validation layer.

The authoritative logic is **`validate_transition`** in `rust-engine/src/engine.rs` alongside the **`RunState`** definition.

## Tokens and idempotency

State updates carry a **transition token** (UUID). Re-applying the **same** token and transition is treated as **idempotent** (see engine tests: duplicate tokens are safe). This supports retries and duplicate delivery without double-applying distinct work.

## Transition hooks

User **`transition_hooks`** observe **successful** transitions **after** commit. Some start paths may emit **multiple** edges in quick succession (for example the batched `PENDING` / `RUNNING` path); see **[Compatibility matrix](../compatibility.md)** for hook ordering details.
