# Tasks

A **task** is a Python callable decorated with **`@task`** from **`prefect_compat`**. Each scheduled execution becomes a **task run** with its own state machine in the Rust engine, tied to the parent **flow run**.

## Basics

- **`task.submit(*args, wait_for=...)`** — schedule work and get a **future** immediately under **`ThreadPoolTaskRunner`** (default); use **`future.result()`** / **`future.wait()`** or **`wait([...])`** to block inside the flow function. Independent submits can overlap; **`wait_for`** gates start. **`wait_for`** also accepts **`SubflowFuture`** from deployment-backed subflows — see **[How to compose flows with subflows](../how-to/subflows.md)**.
- **`@task(name="custom-name")`** — optional runtime task name (defaults to the function name). The static planner resolves names from task objects in the flow module or closure so forecast/DAG labels match task runs.
- **Tags / concurrency limits** — `@task(tags=...)` with `create_tag_concurrency_limit` / named `concurrency` / `rate_limit`. Guide: **[How to use concurrency limits](../how-to/concurrency-limits.md)**. Deployment-level concurrency caps are separate.
- **`task.map(values, wait_for=...)`** — fan out over inputs; returns a list of futures. Combine with **`wait(mapped)`** before downstream **`submit`** calls. All mapped task runs share one **Aggregated fan-out** DAG node in forecast/UI (fan-out collapsed). Parallelism during `submit` / `map` is controlled by the flow’s **task runner** — see **[How to choose a task runner](../how-to/choose-task-runners.md)**.
- **`SequentialTaskRunner`** keeps `submit` non-overlapping; **`ProcessPoolTaskRunner`** still runs `submit` synchronously (process concurrency is via `map` only).
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

## Persist results and resume on retry

On **flow-run resume** (deployment retry or `prepare_resume`), IronFlow may skip already-**COMPLETED** DAG nodes when **flow/deployment parameters** and **submit/`map` inputs** still match:

- Return value **`None`** → auto skip (marker only)
- Non-`None` → skip only with **`@task(persist_result=True)`** and a **JSON-safe** payload (size-capped)
- `map` children key by `map_index` + input fingerprint

```python
@task(persist_result=True)
def expensive(x: int) -> dict:
    return {"x": x, "n": 42}
```

Fresh runs never auto-hit. Cache hits do not re-fire `transition_hooks`. This is **not** Prefect `cache_policy` parity. Full guide: **[How to resume tasks and persist results](../how-to/task-resume-and-persist.md)**.

## Transition hooks

Tasks accept the same **`transition_hooks`** mechanism as flows: **`TransitionHookSpec`** + **`on_transition`**, with optional **`from_state`** / **`to_state`** filters. See **[Flows](flows.md)** and the compatibility matrix for behavior and differences from Prefect’s hook names.
