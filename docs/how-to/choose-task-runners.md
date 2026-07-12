# How to choose a task runner

A **task runner** controls how **`task.map()`** fans out work inside a `@flow`. It is **not** the same as a deployment **worker** or **work pool** — those claim queued deployment runs from the control plane. This guide helps you pick a runner for common workload shapes (remote API calls vs local Python).

Conceptual reference: **[Runners](../concepts/runners.md)** · **[Tasks](../concepts/tasks.md)** · Environment variables: **[`IRONFLOW_TASK_RUNNER`](../reference/env-vars.md)**.

## Task runners vs deployment workers

| Term | What it does |
| --- | --- |
| **Task runner** (`ThreadPoolTaskRunner`, …) | Parallelizes **`map()`** inside a flow that is already executing |
| **Deployment worker** (`ironflow worker start`, embedded server worker) | Claims **deployment runs** and runs the whole `@flow` in a Python process |

You can run API-wrapper flows on a normal **process** work-pool worker and still use **`ThreadPoolTaskRunner`** inside the flow for concurrent `map()` calls.

## What task runners affect (MVP)

**Only `task.map()` parallelism.** In the current MVP, **`task.submit()` runs the task body synchronously** before returning the future. Dependency chains built with `.submit()` and `wait_for` therefore execute **one task at a time**, regardless of the runner.

For concurrent remote calls or parallel fan-out, use **`map()`** (or structure work so independent branches can run via `map`, not a chain of `submit` calls).

## Quick decision table

| Your tasks mostly… | Runner | Typical pattern |
| --- | --- | --- |
| Call HTTP APIs, SDKs, queues, or poll remote jobs (I/O-bound) | **`ThreadPoolTaskRunner`** (default) | `call_remote.map(urls)` |
| Run CPU-heavy pure Python (numeric work, compression, …) | **`ProcessPoolTaskRunner`** | Picklable top-level task + `heavy.map(items)` |
| Need deterministic order or easier debugging | **`SequentialTaskRunner`** | `step.map([1, 2, 3])` runs in order |
| Are a short dependency chain via `.submit()` | Runner choice **barely matters** | Sequential execution either way |
| Fan out once with a single `map` value | Default is fine | Runner falls back to single-threaded path |

**Default:** If you do nothing, IronFlow uses **`ThreadPoolTaskRunner`** via `IRONFLOW_TASK_RUNNER=thread`. That is the right default for most flows, including thin API-wrapper tasks.

## Mechanism 1 — Thread pool (I/O-bound, default)

Use when tasks spend time **waiting** on network, disk, or external services. Threads release the GIL during many I/O operations, so `map()` can overlap multiple API calls.

```python
from prefect_compat import flow, task, wait
from prefect_compat.task_runners import ThreadPoolTaskRunner

@task
def fetch_status(job_id: str) -> dict:
    # Thin wrapper around a remote API — runs in a worker thread during map()
    ...

@flow(task_runner=ThreadPoolTaskRunner(max_workers=8))
def poll_jobs(job_ids: list[str]) -> list[dict]:
    futures = fetch_status.map(job_ids)
    wait(futures)
    return [f.result() for f in futures]
```

**Tuning:** pass `max_workers` on the runner, or set **`IRONFLOW_TASK_RUNNER_THREAD_POOL_MAX_WORKERS`** globally. When unset, the pool size defaults to `min(32, cpu_count + 4)`.

**When thread pool does *not* help:** CPU-bound pure Python in every mapped task (GIL-bound). Prefer a process runner or move hot paths to Rust/native code.

## Mechanism 2 — Process pool (CPU-bound local Python)

Use when mapped task bodies are **CPU-heavy** and can run in child processes. The callable must be **picklable** — in practice, use a **top-level function** in an importable module (see `prefect_compat.mp_picklable` and `python-shim/tests/test_task_runners.py`).

```python
from prefect_compat import flow, task, wait
from prefect_compat.mp_picklable import inc  # top-level, stable import path
from prefect_compat.task_runners import ProcessPoolTaskRunner

_doubled = task(inc)

@flow(task_runner=ProcessPoolTaskRunner(max_workers=4))
def crunch(nums: list[int]) -> int:
    futures = _doubled.map(nums)
    wait(futures)
    return sum(f.result() for f in futures)
```

**Gotchas:**

- Closures, lambdas, and tasks defined inside test modules often **fail to pickle**.
- **Windows** multiprocessing from pytest is unreliable; process-pool `map` is primarily validated on Linux/macOS.
- Process startup has overhead — not worth it for thin API wrappers.

**Tuning:** `max_workers` on the runner or **`IRONFLOW_TASK_RUNNER_PROCESS_POOL_MAX_WORKERS`**.

## Mechanism 3 — Sequential (deterministic)

Use for debugging, reproducible ordering, or tiny fan-outs where concurrency adds no value.

```python
from prefect_compat import flow, task
from prefect_compat.task_runners import SequentialTaskRunner

@task
def tag(x: int) -> int:
    return x

@flow(task_runner=SequentialTaskRunner())
def ordered() -> list[int]:
    return [t.result() for t in tag.map([1, 2, 3])]
```

## Configuration without changing code

Set the default runner for all flows that do not pass `task_runner=`:

```bash
export IRONFLOW_TASK_RUNNER=thread    # default
export IRONFLOW_TASK_RUNNER=sequential  # or seq, serial
export IRONFLOW_TASK_RUNNER=process     # or multiprocessing, mp
```

Per-flow overrides always win: `@flow(task_runner=ThreadPoolTaskRunner(max_workers=4))`.

## Common mistakes

1. **Expecting `submit()` to run in parallel** — use `map()` for fan-out, or accept sequential `submit` chains.
2. **Using `ProcessPoolTaskRunner` for API calls** — adds pickling overhead with no I/O benefit; use threads.
3. **Confusing task runner with work pool** — API flows still need a deployment worker (embedded or `ironflow worker start`); the thread runner only affects in-flow `map()`.
4. **Huge `max_workers` against rate-limited APIs** — cap `max_workers` to respect remote quotas.

## Related docs

- **[Runners](../concepts/runners.md)** — built-in runner types and API surface.
- **[Tasks](../concepts/tasks.md)** — `submit`, `map`, futures.
- **[Self-hosted server](../SELF_HOSTED_SERVER.md)** — deployment workers and work pools.
- **[Performance methodology](../perf_methodology.md)** — how runners interact with the control plane in benchmarks.
