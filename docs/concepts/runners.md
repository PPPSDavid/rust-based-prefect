# Runners

A **task runner** controls how **`map`** (and related scheduling) executes work: sequentially, in a thread pool, or via a process pool for picklable callables.

Task runners are **not** deployment **workers** or **work pools**. Workers claim deployment runs from the control plane; task runners affect parallelism inside a running flow for both **`submit()`** and **`map()`**.

## Built-in runners

`prefect_compat` exposes:

| Runner | Role |
| --- | --- |
| **`SequentialTaskRunner`** | Single-threaded `submit` / `map`; bodies run on the coordinating thread (no overlap). |
| **`ThreadPoolTaskRunner`** | Concurrent `submit` and `map` via `ThreadPoolExecutor` (Prefect 3–style default). Optional `max_workers`; pool size can also follow **`IRONFLOW_TASK_RUNNER_THREAD_POOL_MAX_WORKERS`**. |
| **`ProcessPoolTaskRunner`** | Picklable task bodies in **registered child processes** (cancel / terminate-pause can SIGTERM→SIGKILL). `submit` and `map` return futures; helper threads wait on children. Optional **`IRONFLOW_TASK_RUNNER_PROCESS_POOL_MAX_WORKERS`**. |

## Choosing a runner

- Pass a runner to **`@flow(task_runner=...)`** (see examples in `python-shim/src/prefect_compat/server.py` and tests under `python-shim/tests/`).
- **`default_task_runner_from_env()`** picks a runner from **`IRONFLOW_TASK_RUNNER`**: `thread` (default), `sequential` / `seq` / `serial`, or `process` / `multiprocessing` / `mp`.

**Workload guide:** **[How to choose a task runner](../how-to/choose-task-runners.md)** — API/remote vs local CPU, `submit` vs `map`, and common mistakes.

### `submit` vs `map`

With **`ThreadPoolTaskRunner`** (the default), independent **`task.submit()`** calls return a future immediately and run task bodies concurrently in the flow’s shared thread pool. Use **`wait_for`** (or pass upstream futures as args) so dependents start only after upstream work finishes. **`map()`** remains the preferred fan-out for many inputs; both paths share the same runner semantics for threads.

**`SequentialTaskRunner`** keeps `submit` / `map` non-overlapping. **`ProcessPoolTaskRunner`** parallelizes both **`submit()`** and **`map()`** across registered child processes (use when hard terminate/cancel must stop blind sleeps).

For execution semantics of `submit` / `map` themselves, see **[Tasks](tasks.md)** and **[Compatibility matrix](../compatibility.md)**.
