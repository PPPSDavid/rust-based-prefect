# How to run workers in HTTP mode

Tier **B2** workers claim and finish deployment runs over the API. They never open
the control-plane SQLite/Postgres files. Use this mode for multi-host / compose
layouts (no shared volume).

**Prefect example to borrow from:** the worker service in [Prefect Docker Compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose) — workers talk to the API, not a shared DB volume. FlowOxide’s `FLOWOXIDE_WORKER_MODE=http` is that shape.

## Modes

| Mode | Env / flag | Opens DB? | Needs |
| --- | --- | --- | --- |
| `file` (default) | `FLOWOXIDE_WORKER_MODE=file` | Yes — same `FLOWOXIDE_HISTORY_PATH` / DSN as the server | Shared filesystem or co-located process |
| `http` | `FLOWOXIDE_WORKER_MODE=http` | No | `FLOWOXIDE_API_URL` (+ `FLOWOXIDE_API_AUTH_STRING` when auth is on) |

File mode remains the local/dev default. Prefer **http** in containers. Removing
`file` mode is deferred to a future major version.

## Quick start

On the API server:

```bash
export FLOWOXIDE_ENABLE_LOCAL_WORKER=0
# optional: FLOWOXIDE_DATABASE_URL=postgresql://...
uv run python -m uvicorn python-shim.src.prefect_compat.server:app --host 0.0.0.0 --port 8000
```

On each worker host (flows must be importable — same image or `entrypoint`):

```bash
export FLOWOXIDE_WORKER_MODE=http
export FLOWOXIDE_API_URL=http://api:8000
# export FLOWOXIDE_API_AUTH_STRING='user:pass'  # if server auth is enabled
flowoxide worker start --pool default-process-pool --name worker-1
```

Or: `flowoxide worker start --worker-mode http --api-url http://api:8000`.

## Protocol

```
worker → POST /api/workers/heartbeat
       → POST /api/workers/claim   (204 if empty; claim includes deployment.flow_name/entrypoint)
       → run flow locally
       → POST /api/workers/runs/{id}/started|finished
```

Claim/lease/concurrency rules match the in-process path (Rust when `bind_db`
is active). Empty queue returns **204**. Auth matches other `/api/*` routes.

## Related

- [Postgres control plane](database-postgres.md)
- [Deploy with CLI](deploy-with-cli.md)
- [Env vars](../reference/env-vars.md)
