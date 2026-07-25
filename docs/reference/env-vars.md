# Environment variables

FlowOxide configuration uses `FLOWOXIDE_*` environment variables. Defaults assume a local development stack on `127.0.0.1:8000`.

## Persistence and data paths

| Variable | Default | Description |
| --- | --- | --- |
| `FLOWOXIDE_HISTORY_PATH` | `data/flowoxide_history.jsonl` (when server defaults apply) | JSONL append-only history file. A SQLite sidecar (`.db` next to the JSONL path) powers query APIs in file mode. Server and standalone workers must share the same path when not using Postgres + HTTP workers. |
| `FLOWOXIDE_DATABASE_URL` | *(unset)* | When set to a `postgresql://` / `postgres://` DSN, use Postgres for the control-plane schema instead of the SQLite sidecar. Rust `bind_db` receives the DSN for claim/lease hot paths. Local default remains SQLite when unset. |

## Rust engine

| Variable | Default | Description |
| --- | --- | --- |
| `FLOWOXIDE_RUST_LIB` | *(auto-discover)* | Absolute path to the `flowoxide_engine` shared library when not found under `rust-engine/target/` or the wheel's `prefect_compat/native/`. |
| `FLOWOXIDE_USE_RUST_FSM` | `1` | Set to `0`, `false`, or `no` to force Python-side FSM paths (testing / fallback). |

## API server (embedded worker and scheduler)

| Variable | Default | Description |
| --- | --- | --- |
| `FLOWOXIDE_ENABLE_SCHEDULER` | `1` | Set to `0`, `false`, or `no` to disable the maintenance thread (schedule ticks, stale lease cleanup). In compose, set `0` on the API and run `flowoxide server services start` instead. |
| `FLOWOXIDE_ENABLE_LOCAL_WORKER` | `1` | Set to `0`, `false`, or `no` to disable the in-process worker loop in the API process. Use when running `flowoxide worker start` separately. |
| `FLOWOXIDE_LOCAL_WORKER_NAME` | `local-worker-1` | Worker identity for the embedded local worker. |
| `FLOWOXIDE_WORK_POOL` | `default-process-pool` | Default work pool for embedded worker claims and deployment defaults. |
| `FLOWOXIDE_SCHEDULER_INTERVAL_MS` | `1000` | Scheduler tick interval in milliseconds (API embed or `flowoxide server services start`). |
| `FLOWOXIDE_SCHEDULER_STALE_SECONDS` | `120` | Stale worker lease threshold for maintenance. |
| `FLOWOXIDE_TASK_TAG_SLOT_WAIT_SECONDS` | `1.0` | Poll interval while waiting for tag concurrency slots before entering `Running`. |

## CLI and HTTP client

| Variable | Default | Description |
| --- | --- | --- |
| `FLOWOXIDE_API_URL` | `http://127.0.0.1:8000` | Base URL for `flowoxide deploy`, `flowoxide serve`, HTTP workers, and related CLI commands. |
| `FLOWOXIDE_WORKER_MODE` | `file` | Worker claim transport: `file` (shared DB / history path) or `http` (API claim only; no local store). Use `http` in multi-host / compose layouts. |

## Security (self-hosted)

| Variable | Default | Description |
| --- | --- | --- |
| `FLOWOXIDE_SERVER_API_AUTH_STRING` | *(unset)* | When set (`user:pass`), require HTTP Basic auth on `/api/*`. `/health` stays open. Mirrors Prefect `PREFECT_SERVER_API_AUTH_STRING`. |
| `FLOWOXIDE_API_AUTH_STRING` | *(unset)* | Client credential string for CLI and HTTP clients (`flowoxide deploy`, `DeployClient`). Mirrors Prefect `PREFECT_API_AUTH_STRING`. |

See [Secure a self-hosted server](../how-to/secure-self-hosted.md).

## Task runners

| Variable | Default | Description |
| --- | --- | --- |
| `FLOWOXIDE_TASK_RUNNER` | `thread` | Default runner kind: `sequential`, `thread`, or `process`. |
| `FLOWOXIDE_TASK_RUNNER_THREAD_POOL_MAX_WORKERS` | *(unset)* | Cap thread-pool workers when using the thread runner. |
| `FLOWOXIDE_TASK_RUNNER_PROCESS_POOL_MAX_WORKERS` | *(unset)* | Cap process-pool workers when using the process runner. |

See [Runners](../concepts/runners.md) for behavior details and **[How to choose a task runner](../how-to/choose-task-runners.md)** for workload-based selection.

## Development and packaging (contributors)

| Variable | Default | Description |
| --- | --- | --- |
| `FLOWOXIDE_SKIP_NATIVE_BUILD` | *(unset)* | Skip staging the native library during wheel builds. |
| `FLOWOXIDE_FORCE_PLATFORM_WHEEL` | *(unset)* | Force platform wheel packaging behavior during builds. |
| `PYTHONPATH` | *(unset)* | Set to `python-shim/src` at the repo root for editable-style imports without `pip install -e`. |

## Related docs

- [How to set up FlowOxide](../how-to/setup.md) — clone, build, verify.
- [Self-hosted server](../SELF_HOSTED_SERVER.md) — worker/scheduler toggles in production-like setups.
- [Troubleshooting](troubleshooting.md) — when `native_library_available()` is `False` or workers do not claim runs.
