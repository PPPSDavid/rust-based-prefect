# REST API overview

FlowOxide exposes a FastAPI application at `http://127.0.0.1:8000` by default.

**Authentication (optional):** set `FLOWOXIDE_SERVER_API_AUTH_STRING` on the server and `FLOWOXIDE_API_AUTH_STRING` on clients. When enabled, `/api/*` requires HTTP Basic auth; `/health` does not. See [Secure a self-hosted server](../how-to/secure-self-hosted.md).

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
| `GET` | `/health` | Liveness check |
| `GET` | `/history/summary` | History file stats |

## Flow runs

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/flow-runs` | List flow runs (`state`, `limit`, `cursor`) |
| `GET` | `/api/flow-runs/{flow_run_id}` | Flow run detail |
| `GET` | `/api/flow-runs/{flow_run_id}/task-runs` | Task runs under a flow |
| `GET` | `/api/flow-runs/{flow_run_id}/logs` | Logs (`task_run_id`, `level`, `limit`, `cursor`) |
| `GET` | `/api/flow-runs/{flow_run_id}/events` | Control-plane events |
| `GET` | `/api/flow-runs/{flow_run_id}/dag` | DAG payload for UI |
| `POST` | `/api/flow-runs/{flow_run_id}/cancel` | Cancel (idempotent for terminal states) |
| `POST` | `/api/flow-runs/{flow_run_id}/retry` | Retry deployment-backed runs (`409` otherwise) |

## Flows and tasks (registry)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/flows` | Registered flow catalog |
| `GET` | `/api/flows/{flow_name}` | Flow metadata |
| `GET` | `/api/tasks` | Task catalog |

## Deployments

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/deployments` | List deployments |
| `POST` | `/api/deployments` | Create deployment |
| `GET` | `/api/deployments/{deployment_id}` | Deployment detail |
| `GET` | `/api/deployments/by-name/{name}` | Lookup by unique name |
| `PATCH` | `/api/deployments/{deployment_id}` | Update schedule, pause, parameters |
| `POST` | `/api/deployments/{deployment_id}/run` | Enqueue a deployment run |
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
- [Environment variables](env-vars.md) — `FLOWOXIDE_HISTORY_PATH`, worker toggles.

Maintainer contract notes (internal): `docs/ui_phase1_api_contract.md` in the repository.
