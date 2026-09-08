# UI Phase 1 API Contract

This contract defines the run-centric API surface used by the initial Prefect-comparable UI slice.

## Pagination Model

- Cursor pagination uses a monotonic `seq` in backend storage.
- Request: `?limit=<n>&cursor=<opaque-string>`
- Response:
  - `items`: array of resources
  - `next_cursor`: cursor to request the next page, or `null`

```json
{
  "items": [],
  "next_cursor": "12345"
}
```

## `GET /api/flow-runs`

List flow runs for the runs table.

### Query params

- `state` (optional): one of `SCHEDULED|PENDING|RUNNING|COMPLETED|FAILED|CANCELLED|PAUSED`
- `limit` (optional, default `50`, max `500`)
- `cursor` (optional): opaque keyset cursor. Default sort emits a plain `seq` string (backward compatible). Non-default `sort` emits a `v1.`-prefixed cursor; changing sort/order requires clearing the cursor (`400` if mismatched).
- `include_archived` (optional, default `false`)
- `flow_name` (optional): exact match on flow run `name`
- `deployment_id` (optional): runs linked via `deployment_runs`
- `created_after` / `created_before` (optional): ISO-8601 bounds on `created_at` (inclusive)
- `q` (optional): case-sensitive substring match on flow run `name` (`LIKE`, `%`/`_` escaped)
- `sort` (optional, default `seq`): `seq|created_at|updated_at|name|state`
- `order` (optional, default `desc`): `asc|desc`

### Item shape

```json
{
  "id": "uuid",
  "name": "mapped_flow",
  "state": "COMPLETED",
  "version": 3,
  "created_at": "2026-04-15T21:00:00+00:00",
  "updated_at": "2026-04-15T21:00:02+00:00"
}
```

## `GET /api/flow-runs/{flow_run_id}`

Get a single flow run detail header.

## `GET /api/flow-runs/{flow_run_id}/task-runs`

List task runs under a flow run.

### Query params

- `limit` (optional, default `200`, max `1000`)
- `cursor` (optional)

### Item shape

```json
{
  "id": "uuid",
  "flow_run_id": "uuid",
  "task_name": "inc",
  "state": "COMPLETED",
  "version": 3,
  "created_at": "2026-04-15T21:00:01+00:00",
  "updated_at": "2026-04-15T21:00:02+00:00"
}
```

## `GET /api/flow-runs/{flow_run_id}/logs`

List logs for a flow run (optionally scoped to a task run).

### Query params

- `task_run_id` (optional)
- `level` (optional, uppercase log level)
- `limit` (optional, default `500`, max `2000`)
- `cursor` (optional)

### Item shape

```json
{
  "id": "uuid",
  "flow_run_id": "uuid",
  "task_run_id": "uuid-or-null",
  "level": "INFO",
  "message": "inc: task_completed",
  "timestamp": "2026-04-15T21:00:02+00:00"
}
```

## Compatibility/Expansion Endpoints (Phases 2-4)

- `GET /api/flows`
- `GET /api/flows/{flow_name}`
- `GET /api/tasks`
- `GET /api/flow-runs/{flow_run_id}/events`
- `GET /api/flow-runs/{flow_run_id}/artifacts`
- `GET /api/task-runs/{task_run_id}/artifacts`
- `GET /api/artifacts/{artifact_id}`
- `GET /api/stream/flow-runs`
- `GET /api/stream/flow-runs/{flow_run_id}`

## Phase 5 mutation endpoints (UI actions)

- `POST /api/flow-runs/{flow_run_id}/cancel` — user-initiated cancel (idempotent for terminal states)
- `POST /api/flow-runs/{flow_run_id}/retry` — re-trigger deployment run when flow run is deployment-backed (`409` otherwise)
- `GET /api/deployments/{deployment_id}`
- `GET /api/work-pools`, `POST /api/work-pools`, `PATCH /api/work-pools/{id}`
- `GET /api/workers`, `POST /api/workers/heartbeat`

## Performance Targets (Balanced Profile)

- `GET /api/flow-runs` p95 <= `120ms`
- `GET /api/flow-runs/{id}/task-runs` p95 <= `150ms`
- `GET /api/flow-runs/{id}/logs` p95 <= `200ms`
