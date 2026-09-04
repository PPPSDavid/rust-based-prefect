# REST API overview

IronFlow exposes a FastAPI application at `http://127.0.0.1:8000` by default.

**Authentication (optional):** set `IRONFLOW_SERVER_API_AUTH_STRING` on the server and `IRONFLOW_API_AUTH_STRING` on clients. When enabled, `/api/*` requires HTTP Basic auth; `/health` does not. See [Secure a self-hosted server](../how-to/secure-self-hosted.md).

**Interactive docs:** `GET /docs` (Swagger UI) and `GET /redoc` when the server is running.

Start the server:

```bash
python -m uvicorn prefect_compat.server:app --host 127.0.0.1 --port 8000
```

## Pagination

List endpoints use cursor pagination:

- Request: `?limit=<n>&cursor=<opaque-string>`
- Response: `{ "items": [...], "next_cursor": "<string-or-null>" }`

Monotonic `seq` in storage backs cursors.

## Health and diagnostics

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/server-info` | Liveness plus catalog env flags (`catalog_hide_archived`, `run_retention_days`, `orphan_flow_gc`) |
| `GET` | `/history/summary` | History file stats |

## Flow runs

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/flow-runs` | List flow runs (`state`, `limit`, `cursor`, `include_archived`) |
| `GET` | `/api/flow-runs/{flow_run_id}` | Flow run detail |
| `GET` | `/api/flow-runs/{flow_run_id}/task-runs` | Task runs under a flow |
| `GET` | `/api/flow-runs/{flow_run_id}/logs` | Logs (`task_run_id`, `level`, `limit`, `cursor`) |
| `GET` | `/api/flow-runs/{flow_run_id}/events` | Control-plane events |
| `GET` | `/api/flow-runs/{flow_run_id}/dag` | DAG payload for UI |
| `POST` | `/api/flow-runs/{flow_run_id}/cancel` | Cancel (terminate semantics; process workers SIGTERM→SIGKILL when registered) |
| `POST` | `/api/flow-runs/{flow_run_id}/pause` | Operator pause — **requires** JSON `{"mode":"drain"}` or `{"mode":"terminate"}` (`422` if missing/invalid). Not for gate-only `PAUSED`. |
| `POST` | `/api/flow-runs/{flow_run_id}/resume` | Resume an operator pause only (`400` for gate-only pause). See **[cancel / pause / resume](../how-to/cancel-pause-resume.md)**. |
| `POST` | `/api/flow-runs/{flow_run_id}/retry` | Retry deployment-backed runs (`409` otherwise). New run carries resume lineage so eligible completed tasks may skip — see **[task resume how-to](../how-to/task-resume-and-persist.md)**. |

## Flows and tasks (registry)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/flows` | Flow catalog (`status=active\|archived`) |
| `GET` | `/api/flows/{flow_name}` | Flow metadata (aliases resolve) |
| `POST` | `/api/flows/{flow_id}/rename` | Catalog-only rename (409 if undeleted deployments). Prefer `formerly=` + `POST /api/deployments/apply` when source still exists — **[rename how-to](../how-to/rename-archive-flows.md)**. |
| `POST` | `/api/flows/{flow_id}/archive` | Archive (409 if undeleted deployments) |
| `POST` | `/api/flows/{flow_id}/restore` | Restore archived/deleted |
| `DELETE` | `/api/flows/{flow_id}` | Soft-delete (409 if undeleted deployments or live runs) |
| `GET` | `/api/tasks` | Task catalog |

## Deployments

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/deployments` | List deployments |
| `POST` | `/api/deployments` | Create deployment |
| `GET` | `/api/deployments/{deployment_id}` | Deployment detail |
| `GET` | `/api/deployments/by-name/{name}` | Lookup by unique name |
| `PATCH` | `/api/deployments/{deployment_id}` | Update schedule, pause, parameters |
| `POST` | `/api/deployments/apply` | Primary source rename: upsert + optional prune/`formerly=` (`prune=true`) |
| `DELETE` | `/api/deployments/{deployment_id}` | Soft-delete (409 if live runs or schedule enabled) |
| `GET` | `/api/deployment-runs` | List deployment runs |

## Work pools and workers

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/work-pools` | List work pools |
| `POST` | `/api/work-pools` | Create work pool |
| `GET` | `/api/work-pools/{work_pool_id}` | Pool detail |
| `PATCH` | `/api/work-pools/{work_pool_id}` | Update pool |
| `GET` | `/api/workers` | List workers |
| `POST` | `/api/workers/heartbeat` | Worker heartbeat |

Only **process** work pools are supported today.

## Artifacts and streaming

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/flow-runs/{flow_run_id}/artifacts` | Flow-scoped artifacts |
| `GET` | `/api/task-runs/{task_run_id}/artifacts` | Task-scoped artifacts |
| `GET` | `/api/artifacts/{artifact_id}` | Single artifact |
| `GET` | `/api/stream/flow-runs` | SSE: flow-run list changes |
| `GET` | `/api/stream/flow-runs/{flow_run_id}` | SSE: single run updates |

## Benchmark (development)

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/benchmark/run` | Run built-in benchmark flavors (`simple`, `wide`, `long_chain`, `failing`, aliases `mapped`, `chained`) |

## Related docs

- [How to create and update deployments](../how-to/deployments.md) — curl examples.
- [Self-hosted server](../SELF_HOSTED_SERVER.md) — workers and schedules.
- [Environment variables](env-vars.md) — `IRONFLOW_HISTORY_PATH`, worker toggles.

Maintainer contract notes (internal): `docs/ui_phase1_api_contract.md` in the repository.
