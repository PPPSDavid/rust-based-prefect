# Flows

A **flow** is a Python function decorated with **`@flow`** from **`prefect_compat`** (not `prefect`). When you invoke it, IronFlow creates a **flow run** in the control plane: the Rust engine records state transitions and append-only history for that run and its task runs.

## Basics

- Import: `from prefect_compat import flow` (see [Prefect → IronFlow](../PREFECT_IRONFLOW_MAPPING.md)).
- A flow coordinates **task runs** by calling **`task.submit(...)`**, **`task.map(...)`**, and **`wait(...)`** on futures—see **[Tasks](tasks.md)**.
- You typically register a control plane (for example **`InMemoryControlPlane`**) with **`set_control_plane`** before executing the flow; see **[Quick start (demo flow)](../QUICKSTART_DEMO.md)**.

## Final state (`wait_all` default)

By default IronFlow uses **`@flow(final_state="wait_all")`**: after the flow body returns, it drains in-process submits, waits for non-detached children (including deployment-backed subflows), then resolves the flow terminal state in **Rust** from contributing task-run rows (`CANCELLED` > `FAILED` > all `COMPLETED`). Unobserved failed concurrent submits fail the flow (`FlowChildrenFailed`).

Escape hatches:

- **`submit(..., detach=True)`** — exclude one task/subflow from the wait set (true fire-and-forget / safe-to-fail).
- **`@flow(final_state="explicit")`** — body return/exception remains authoritative (closer to Prefect’s return-value model).

Design notes: **[flow-run final state plan](../plans/flow-run-final-state.md)**. Compatibility: **[Compatibility matrix](../compatibility.md)**.

## Subflows (nesting flows)

IronFlow supports **two** nesting mechanisms:

1. **Inline (blocking)** — call another `@flow` as a normal Python function inside the parent. Same process; parent waits for the return value; child run is linked (`execution_mode=inline`).
2. **Deployment-backed (subflow as task)** — `deployment_ref("deployment-name").submit(...).result()` enqueues work on a deployment’s work pool. Returns a **`SubflowFuture`** that works with **`wait_for`** / **`wait`**. Under `wait_all`, omitting `.result()` still waits unless **`detach=True`**.

Step-by-step examples, UI notes, and choosing between the two: **[How to compose flows with subflows](../how-to/subflows.md)**. Supported subset and limits: **[Compatibility matrix](../compatibility.md)**.

## Transition hooks (IronFlow extension)

Flows support **`transition_hooks`**: a sequence of **`TransitionHookSpec`** values built with **`on_transition(fn, from_state=..., to_state=...)`**. Use **`None`** for `from_state` or `to_state` to match any state on that side.

Hooks run **after** a successful control-plane transition, **in process**, without holding the control-plane lock. They are **not** the same API names as Prefect’s `on_running` / `on_failure` hooks; map your logic to explicit **edges** (for example `PENDING` → `RUNNING`). For full semantics (including the batched start path and error handling), see **[Compatibility matrix](../compatibility.md)**.

Relevant exports: `TransitionHookSpec`, `on_transition`, `TransitionContext` from `prefect_compat`.
