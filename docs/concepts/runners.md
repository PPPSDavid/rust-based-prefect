# Runners

A **task runner** controls how **`map`** (and related scheduling) executes work: sequentially, in a thread pool, or via a process pool for picklable callables.

Task runners are **not** deployment **workers** or **work pools**. Workers claim deployment runs from the control plane; task runners affect parallelism inside a running flow for both **`submit()`** and **`map()`**.

## Built-in runners

`prefect_compat` exposes:

| Runner | Role |
| --- | --- |
| **`SequentialTaskRunner`** | Single-threaded `submit` / `map`; bodies run on the coordinating thread (no overlap). |
| **`ThreadPoolTaskRunner`** | Concurrent `submit` and `map` via `ThreadPoolExecutor` (Prefect 3–style default). Optional `max_workers`; pool size can also follow **`IRONFLOW_TASK_RUNNER_THREAD_POOL_MAX_WORKERS`**. |
| **`ProcessPoolTaskRunner`** | Concurrent `submit` / `map` for picklable tasks; optional **`IRONFLOW_TASK_RUNNER_PROCESS_POOL_MAX_WORKERS`**. Orchestration (wait_for, tags, FSM) uses a thread pool; bodies run in child processes. |

## Choosing a runner

- Pass a runner to **`@flow(task_runner=...)`** (see examples in `python-shim/src/prefect_compat/server.py` and tests under `python-shim/tests/`).
- **`default_task_runner_from_env()`** picks a runner from **`IRONFLOW_TASK_RUNNER`**: `thread` (default), `sequential` / `seq` / `serial`, or `process` / `multiprocessing` / `mp`.

**Workload guide:** **[How to choose a task runner](../how-to/choose-task-runners.md)** — API/remote vs local CPU, `submit` vs `map`, and common mistakes.

### `submit` vs `map`

With **`ThreadPoolTaskRunner`** or **`ProcessPoolTaskRunner`**, independent **`task.submit()`** calls create a **PENDING** task run and return a future immediately. Workers then apply **`wait_for`**, acquire tag slots, promote to **RUNNING**, and run the body. Use **`wait_for`** (or pass upstream futures as args) so dependents do not start early. **`map()`** remains the preferred fan-out for many inputs.

**`SequentialTaskRunner`** keeps `submit` / `map` end-to-end synchronous on the coordinating thread.

For execution semantics of `submit` / `map` themselves, see **[Tasks](tasks.md)** and **[Compatibility matrix](../compatibility.md)**.
