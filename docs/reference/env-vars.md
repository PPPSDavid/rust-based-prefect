# Environment variables

IronFlow configuration uses `IRONFLOW_*` environment variables. Defaults assume a local development stack on `127.0.0.1:8000`.

## Persistence and data paths

| Variable | Default | Description |
| --- | --- | --- |
| `IRONFLOW_HISTORY_PATH` | `data/ironflow_history.jsonl` (when server defaults apply) | JSONL append-only history file. A SQLite sidecar (`.db` next to the JSONL path) powers query APIs in file mode. Server and standalone workers must share the same path when not using Postgres + HTTP workers. |
| `IRONFLOW_DATABASE_URL` | *(unset)* | When set to a `postgresql://` / `postgres://` DSN, use Postgres for the control-plane schema instead of the SQLite sidecar. Rust `bind_db` receives the DSN for claim/lease hot paths. Local default remains SQLite when unset. |

## Rust engine

| Variable | Default | Description |
| --- | --- | --- |
| `IRONFLOW_RUST_LIB` | *(auto-discover)* | Absolute path to the `ironflow_engine` shared library when not found under `rust-engine/target/` or the wheel's `prefect_compat/native/`. |
| `IRONFLOW_USE_RUST_FSM` | `1` | Set to `0`, `false`, or `no` to force Python-side FSM paths (testing / fallback). |

## API server (embedded worker and scheduler)

| Variable | Default | Description |
| --- | --- | --- |
| `IRONFLOW_ENABLE_SCHEDULER` | `1` | Set to `0`, `false`, or `no` to disable the maintenance thread (schedule ticks, stale lease cleanup). In compose, set `0` on the API and run `ironflow server services start` instead. |
| `IRONFLOW_ENABLE_LOCAL_WORKER` | `1` | Set to `0`, `false`, or `no` to disable the in-process worker loop in the API process. Use when running `ironflow worker start` separately. |
| `IRONFLOW_LOCAL_WORKER_NAME` | `local-worker-1` | Worker identity for the embedded local worker. |
| `IRONFLOW_WORK_POOL` | `default-process-pool` | Default work pool for embedded worker claims and deployment defaults. |
| `IRONFLOW_SCHEDULER_INTERVAL_MS` | `1000` | Scheduler tick interval in milliseconds (API embed or `ironflow server services start`). |
| `IRONFLOW_SCHEDULER_STALE_SECONDS` | `120` | Stale worker lease threshold for maintenance. |
| `IRONFLOW_TASK_TAG_SLOT_WAIT_SECONDS` | `1.0` | Poll interval while waiting for tag concurrency slots before entering `Running`. |

## CLI and HTTP client

| Variable | Default | Description |
| --- | --- | --- |
| `IRONFLOW_API_URL` | `http://127.0.0.1:8000` | Base URL for `ironflow deploy`, `ironflow serve`, HTTP workers, and related CLI commands. |
| `IRONFLOW_WORKER_MODE` | `file` | Worker claim transport: `file` (shared DB / history path) or `http` (API claim only; no local store). Use `http` in multi-host / compose layouts. |

## Security (self-hosted)

| Variable | Default | Description |
| --- | --- | --- |
| `IRONFLOW_SERVER_API_AUTH_STRING` | *(unset)* | When set (`user:pass`), require HTTP Basic auth on `/api/*`. `/health` stays open. Mirrors Prefect `PREFECT_SERVER_API_AUTH_STRING`. |
| `IRONFLOW_API_AUTH_STRING` | *(unset)* | Client credential string for CLI and HTTP clients (`ironflow deploy`, `DeployClient`). Mirrors Prefect `PREFECT_API_AUTH_STRING`. |

See [Secure a self-hosted server](../how-to/secure-self-hosted.md).

## Task runners

| Variable | Default | Description |
| --- | --- | --- |
| `IRONFLOW_TASK_RUNNER` | `thread` | Default runner kind: `sequential`, `thread`, or `process`. |
| `IRONFLOW_TASK_RUNNER_THREAD_POOL_MAX_WORKERS` | *(unset)* | Cap thread-pool workers when using the thread runner. |
| `IRONFLOW_TASK_RUNNER_PROCESS_POOL_MAX_WORKERS` | *(unset)* | Cap process-pool workers when using the process runner. |

See [Runners](../concepts/runners.md) for behavior details and **[How to choose a task runner](../how-to/choose-task-runners.md)** for workload-based selection.

## Development and packaging (contributors)

| Variable | Default | Description |
| --- | --- | --- |
| `IRONFLOW_SKIP_NATIVE_BUILD` | *(unset)* | Skip staging the native library during wheel builds. |
| `IRONFLOW_FORCE_PLATFORM_WHEEL` | *(unset)* | Force platform wheel packaging behavior during builds. |
| `PYTHONPATH` | *(unset)* | Set to `python-shim/src` at the repo root for editable-style imports without `pip install -e`. |

## Related docs

- [How to set up IronFlow](../how-to/setup.md) — clone, build, verify.
- [Self-hosted server](../SELF_HOSTED_SERVER.md) — worker/scheduler toggles in production-like setups.
- [Troubleshooting](troubleshooting.md) — when `native_library_available()` is `False` or workers do not claim runs.
