# Use Postgres for the control plane (Tier B1)

**Audience:** operators testing multi-process / compose prep  
**Default remains SQLite** for local zero-config.

## When to use it

- You need a networked database shared by an API process (workers still need HTTP claim in B2).
- You want to validate `IRONFLOW_DATABASE_URL` before shipping compose images.

## Setup

1. Run Postgres 16+ and create a database/user.
2. Point the API at the DSN:

```bash
export IRONFLOW_DATABASE_URL=postgresql://ironflow:ironflow@127.0.0.1:5432/ironflow
uv run python -m uvicorn python-shim.src.prefect_compat.server:app --host 127.0.0.1 --port 8000
```

On first connect IronFlow creates the control-plane tables (flow/task runs, deployments, workers, …).

3. Leave `IRONFLOW_DATABASE_URL` unset to keep the SQLite sidecar next to `IRONFLOW_HISTORY_PATH`.

## Behavior notes

- Rust `bind_db` accepts the Postgres DSN for **claim / lease / mark started|finished / attach**.
- Schedule ticks and most CRUD use the Python path on Postgres in this slice (sqlite-shaped SQL via an adapter).
- File-mode workers that open the SQLite file directly are **not** a Postgres substitute — wait for HTTP workers (B2).

See also: [self-hosted storage RFC](../plans/self-hosted-storage-rfc.md), [environment variables](../reference/env-vars.md).
