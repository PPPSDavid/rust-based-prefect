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

- `state` (optional): one of `SCHEDULED|PENDING|RUNNING|COMPLETED|FAILED|CANCELLED`
- `limit` (optional, default `50`, max `500`)
- `cursor` (optional)

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

## Performance Targets (Balanced Profile)

- `GET /api/flow-runs` p95 <= `120ms`
- `GET /api/flow-runs/{id}/task-runs` p95 <= `150ms`
- `GET /api/flow-runs/{id}/logs` p95 <= `200ms`
