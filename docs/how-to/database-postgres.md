# Use Postgres for the control plane (Tier B1)

**Audience:** operators running multi-process or Compose stacks  
**Default remains SQLite** for local zero-config.

**Prefect example to borrow from:** [Local Prefect server — database](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli) (SQLite default; Postgres for multi-process). FlowOxide uses `FLOWOXIDE_DATABASE_URL` instead of Prefect’s asyncpg URL setting; there is no `flowoxide server database upgrade` CLI yet.

## When to use it

- You need a networked database for the API + services process (compose default).
- HTTP workers still talk to the **API**, not the DB — see [HTTP workers](worker-http-mode.md).

## Setup

1. Run Postgres 16+ and create a database/user.
2. Point the API at the DSN:

```bash
export FLOWOXIDE_DATABASE_URL=postgresql://flowoxide:flowoxide@127.0.0.1:5432/flowoxide
uv run python -m uvicorn python-shim.src.prefect_compat.server:app --host 127.0.0.1 --port 8000
```

On first connect FlowOxide creates the control-plane tables (flow/task runs, deployments, workers, …).

3. Leave `FLOWOXIDE_DATABASE_URL` unset to keep the SQLite sidecar next to `FLOWOXIDE_HISTORY_PATH`.

## Behavior notes

- Rust `bind_db` accepts the Postgres DSN for **claim / lease / mark started|finished / attach**.
- Schedule ticks and most CRUD use the Python path on Postgres in this slice (sqlite-shaped SQL via an adapter).
- File-mode workers that open the SQLite file directly are **not** a Postgres substitute — use [HTTP workers](worker-http-mode.md) for multi-process / Compose (do not share a SQLite file with the API).

See also: [Docker Compose](docker-compose.md), [self-hosted storage RFC](../plans/self-hosted-storage-rfc.md), [environment variables](../reference/env-vars.md).
