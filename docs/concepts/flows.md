# Flows

A **flow** is a Python function decorated with **`@flow`** from **`prefect_compat`** (not `prefect`). When you invoke it, IronFlow creates a **flow run** in the control plane: the Rust engine records state transitions and append-only history for that run and its task runs.

## Basics

- Import: `from prefect_compat import flow` (see [Prefect → IronFlow](../PREFECT_IRONFLOW_MAPPING.md)).
- A flow coordinates **task runs** by calling **`task.submit(...)`**, **`task.map(...)`**, and **`wait(...)`** on futures—see **[Tasks](tasks.md)**.
- You typically register a control plane (for example **`InMemoryControlPlane`**) with **`set_control_plane`** before executing the flow; see **[Quick start (demo flow)](../QUICKSTART_DEMO.md)**.

## Catalog identity vs the name in source

The control plane stores a **UUID-stable flow catalog**. `@flow(name=...)` is a **claim** on the next run or deploy, not a live pointer the UI can rewrite.

If you still have the flow file, **that source is the source of truth**. Rename with `@flow(name="B", formerly=["A"])` and `ironflow deploy --all --prune`. Re-running `serve()` with a new name and no `formerly=` forks a second identity.

The UI **Rename** / **Archive** / **Delete** actions are catalog-only. Use them when there is **no undeleted (including paused) deployment** — you never served this flow, or you already removed the deployment — **and** you will not keep executing a file that still claims the old name. Step-by-step: **[How to rename, archive, and delete flows](../how-to/rename-archive-flows.md)**.

## Final state (`wait_all` default)

By default IronFlow uses **`@flow(final_state="wait_all")`**: after the flow body returns, it drains in-process submits, waits for non-detached children (including deployment-backed subflows), then resolves the flow terminal state in **Rust** from contributing task-run rows (`CANCELLED` > `FAILED` > all `COMPLETED`). Unobserved failed concurrent submits fail the flow (`FlowChildrenFailed`).

Escape hatches:

- **`submit(..., detach=True)`** — exclude one task/subflow from the wait set (true fire-and-forget / safe-to-fail).
- **`@flow(final_state="explicit")`** — body return/exception remains authoritative (closer to Prefect’s return-value model).

Design notes: [flow-run final state plan](https://github.com/PPPSDavid/rust-based-prefect/blob/main/docs/plans/flow-run-final-state.md). Compatibility: **[Compatibility matrix](../compatibility.md)**.

## Graph mode

Use **`@flow(graph_mode="auto"|"static"|"dynamic")`** (default **`auto`**) to control resume behavior on retry. Static-effective flows may skip completed DAG nodes when the execution contract matches; dynamic-effective flows always re-execute. See **[Execution contract](../concepts/execution-contract.md)** and **[How to choose graph mode and retry](../how-to/graph-mode-and-retry.md)**.

## Subflows (nesting flows)

IronFlow supports **two** nesting mechanisms:

1. **Inline (blocking)** — call another `@flow` as a normal Python function inside the parent. Same process; parent waits for the return value; child run is linked (`execution_mode=inline`).
2. **Deployment-backed (subflow as task)** — `deployment_ref("deployment-name").submit(...).result()` enqueues work on a deployment’s work pool. Returns a **`SubflowFuture`** that works with **`wait_for`** / **`wait`**. Under `wait_all`, omitting `.result()` still waits unless **`detach=True`**.

Step-by-step examples, UI notes, and choosing between the two: **[How to compose flows with subflows](../how-to/subflows.md)**. Supported subset and limits: **[Compatibility matrix](../compatibility.md)**.

## Transition hooks (IronFlow extension)

Flows support **`transition_hooks`**: a sequence of **`TransitionHookSpec`** values built with **`on_transition(fn, from_state=..., to_state=...)`**. Use **`None`** for `from_state` or `to_state` to match any state on that side.

If `fn` returns **None**, it only observes (notifications, logging). If it returns a legal terminal **`RunState`** (`COMPLETED` / `FAILED` / `CANCELLED`) on a proposed **`RUNNING` → terminal** edge, that return **rewrites** the destination **before** commit. The first legal returned state wins. Remaining `None`-return hooks then run on the **committed** edge. Operator cancel and process-kill paths ignore return values.

They are **not** the same API names as Prefect’s `on_running` / `on_failure` hooks; map notify logic to explicit edges (for example `PENDING` → `RUNNING`). Prefect 3 hooks cannot override destination state. For full semantics, see **[Compatibility matrix](../compatibility.md)**.

Relevant exports: `TransitionHookSpec`, `on_transition`, `TransitionContext`, `TransitionRewriteFailed` from `prefect_compat`.

## Runtime context and logging

Inside an active flow (or task) body:

```python
from prefect_compat import get_run_context, get_run_logger

@flow
def pipeline(n: int) -> int:
    ctx = get_run_context()  # flow_run_id, flow_name, parameters, …
    log = get_run_logger()
    log.info("starting with n=%s run=%s", n, ctx.flow_run_id)
    return n
```

`get_run_logger()` messages appear under **`GET /api/flow-runs/{id}/logs`** and the UI Logs tab. Outside a run, the logger writes to stderr and does not persist. See **[Tasks](tasks.md)** for task-scoped association.

## Operator pause / cancel (subset)

- **Cancel** — `POST /api/flow-runs/{id}/cancel` (always terminate semantics).
- **Pause** — `POST /api/flow-runs/{id}/pause` with required JSON `{"mode": "drain"}` or `{"mode": "terminate"}` (no ambiguous default).
- **Resume** — `POST /api/flow-runs/{id}/resume` for operator pauses only (gate waits are different).

**Drain** lets in-flight tasks finish then holds `PAUSED`; further `submit` in the same in-process body raises `FlowRunSchedulingHeld`. **Terminate** / cancel cancel RUNNING rows (late `COMPLETED` fenced) and, under **`ProcessPoolTaskRunner`**, SIGTERM→SIGKILL registered child processes. Thread-pool bodies remain cooperative-only. After terminate pause, in-process resume prepares P1 lineage for the **next** `@flow()` invoke (prior attempt is terminalized); deployment-backed resume uses retry-with-`resume_from`.

Step-by-step: **[How to cancel, pause, and resume](../how-to/cancel-pause-resume.md)**. Design: [lifecycle plan](https://github.com/PPPSDavid/rust-based-prefect/blob/main/docs/plans/flow-run-lifecycle-control.md).
