# Flows

A **flow** is a Python function decorated with **`@flow`** from **`prefect_compat`** (not `prefect`). When you invoke it, IronFlow creates a **flow run** in the control plane: the Rust engine records state transitions and append-only history for that run and its task runs.

## Basics

- Import: `from prefect_compat import flow` (see [Prefect → IronFlow](../PREFECT_IRONFLOW_MAPPING.md)).
- A flow coordinates **task runs** by calling **`task.submit(...)`**, **`task.map(...)`**, and **`wait(...)`** on futures—see **[Tasks](tasks.md)**.
- You typically register a control plane (for example **`InMemoryControlPlane`**) with **`set_control_plane`** before executing the flow; see **[Quick start (demo flow)](../QUICKSTART_DEMO.md)**.

## Transition hooks (IronFlow extension)

Flows support **`transition_hooks`**: a sequence of **`TransitionHookSpec`** values built with **`on_transition(fn, from_state=..., to_state=...)`**. Use **`None`** for `from_state` or `to_state` to match any state on that side.

Hooks run **after** a successful control-plane transition, **in process**, without holding the control-plane lock. They are **not** the same API names as Prefect’s `on_running` / `on_failure` hooks; map your logic to explicit **edges** (for example `PENDING` → `RUNNING`). For full semantics (including the batched start path and error handling), see **[Compatibility matrix](../compatibility.md)**.

Relevant exports: `TransitionHookSpec`, `on_transition`, `TransitionContext` from `prefect_compat`.
