# Runners

A **task runner** controls how **`map`** (and related scheduling) executes work: sequentially, in a thread pool, or via a process pool for picklable callables.

## Built-in runners

`prefect_compat` exposes:

| Runner | Role |
| --- | --- |
| **`SequentialTaskRunner`** | Single-threaded `map`; deterministic in-process ordering. |
| **`ThreadPoolTaskRunner`** | Concurrent `map` via `ThreadPoolExecutor` (Prefect 3–style default). Optional `max_workers`; pool size can also follow **`IRONFLOW_TASK_RUNNER_THREAD_POOL_MAX_WORKERS`**. |
| **`ProcessPoolTaskRunner`** | Process-pool `map` for picklable tasks; optional **`IRONFLOW_TASK_RUNNER_PROCESS_POOL_MAX_WORKERS`**. |

## Choosing a runner

- Pass a runner to **`@flow(task_runner=...)`** (see examples in `python-shim/src/prefect_compat/server.py` and tests under `python-shim/tests/`).
- **`default_task_runner_from_env()`** picks a runner from **`IRONFLOW_TASK_RUNNER`**: `thread` (default), `sequential` / `seq` / `serial`, or `process` / `multiprocessing` / `mp`.

For execution semantics of `submit` / `map` themselves, see **[Tasks](tasks.md)** and **[Compatibility matrix](../compatibility.md)**.
