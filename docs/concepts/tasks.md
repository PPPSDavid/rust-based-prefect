# Tasks

A **task** is a Python callable decorated with **`@task`** from **`prefect_compat`**. Each scheduled execution becomes a **task run** with its own state machine in the Rust engine, tied to the parent **flow run**.

## Basics

- **`task.submit(*args, wait_for=...)`** — schedule work and get a **future**; use **`future.result()`** or **`wait([...])`** to block inside the flow function. **`wait_for`** also accepts **`SubflowFuture`** from deployment-backed subflows — see **[How to compose flows with subflows](../how-to/subflows.md)**.
- **`@task(name="custom-name")`** — optional runtime task name (defaults to the function name). The static planner resolves names from task objects in the flow module or closure so forecast/DAG labels match task runs.
- **Tags / concurrency limits** — Prefect-style `@task(tags=...)` and global `concurrency` / `rate_limit` slot limits are **not** implemented yet. Deployment-level concurrency caps exist separately. See **`docs/plans/concurrency-limits.md`**.
- **`task.map(values, wait_for=...)`** — fan out over inputs; returns a list of futures. Combine with **`wait(mapped)`** before downstream **`submit`** calls. All mapped task runs share one **Aggregated fan-out** DAG node in forecast/UI (fan-out collapsed). Parallelism during `map` is controlled by the flow’s **task runner** — see **[How to choose a task runner](../how-to/choose-task-runners.md)**.
- **`task.submit()`** — in the current MVP, runs the task body **before** returning the future (sequential execution). For concurrent fan-out (for example overlapping API calls), prefer **`map()`** with **`ThreadPoolTaskRunner`**.
- Imports and patterns match the subset described in **[Compatibility matrix](../compatibility.md)** and the **[Quick start (demo flow)](../QUICKSTART_DEMO.md)** example.

## Repeated and aliased tasks

IronFlow treats each **`submit`** (or **`map`** site) as its own graph node, even when the Python function or task name repeats.

**Same task, multiple calls** (e.g. status at start and end of a flow):

```python
@task(name="status-update")
def notify(msg: str) -> str:
    return msg

@flow
def pipeline() -> str:
    notify.submit("starting")
    return notify.submit("finished").result()
```

Logical DAG labels: `status-update-0`, `status-update-1`. Each call gets its own **`planned_node_id`** and task run. In the UI **Aggregated fan-out** view these appear as separate planned steps; **Task runs** shows each execution.

**Different names, shared implementation** — use separate `TaskWrapper` instances; the graph shows separate nodes (`ping-start-0`, `ping-end-0`) because orchestration identity is the task definition, not the shared `def` body:

```python
def ping_body() -> str:
    return "pong"

start_ping = task(name="ping-start")(ping_body)
end_ping = task(name="ping-end")(ping_body)
```

See **[DAG and forecast](dag-and-forecast.md)** for how this appears in the UI.

## Transition hooks

Tasks accept the same **`transition_hooks`** mechanism as flows: **`TransitionHookSpec`** + **`on_transition`**, with optional **`from_state`** / **`to_state`** filters. See **[Flows](flows.md)** and the compatibility matrix for behavior and differences from Prefect’s hook names.
