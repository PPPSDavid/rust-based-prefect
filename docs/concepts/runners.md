# Runners

A **task runner** controls how **`map`** (and related scheduling) executes work: sequentially, in a thread pool, or via a process pool for picklable callables.

Task runners are **not** deployment **workers** or **work pools**. Workers claim deployment runs from the control plane; task runners only affect parallelism inside a running flow (today: **`map()`** only — see below).

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

**Workload guide:** **[How to choose a task runner](../how-to/choose-task-runners.md)** — API/remote vs local CPU, `submit` vs `map`, and common mistakes.

### MVP caveat: `submit` vs `map`

Task runners parallelize **`map()`** fan-out. **`task.submit()`** runs the task body **synchronously** in the current MVP before returning the future, so `.submit()` chains do not gain concurrency from the runner. Use **`map()`** when you need overlapping I/O-bound or CPU-bound mapped work.

For execution semantics of `submit` / `map` themselves, see **[Tasks](tasks.md)** and **[Compatibility matrix](../compatibility.md)**.
