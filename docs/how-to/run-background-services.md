# How to run background services

The API process can run schedule ticks embedded (`IRONFLOW_ENABLE_SCHEDULER=1`,
default for Tier A). In production compose, disable the embedded scheduler on
the API and run a dedicated **services** process — same split as Prefect’s
`prefect server start --no-services` plus
[`prefect server services start`](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose)
(borrow that guide’s process layout; IronFlow does not yet elect a multi-replica leader).

```bash
# Point at the same store as the API (Postgres recommended).
export IRONFLOW_DATABASE_URL=postgresql://ironflow:ironflow@127.0.0.1:5432/ironflow
ironflow server services start
```

Or use the compose `services` container in [Docker Compose](docker-compose.md).

## What it does

- Prefers Rust `deployment_scheduler_start` when the native engine is bound
- Otherwise polls `deployment_maintenance_tick` (lease reclaim, due schedules, stale workers)

Env:

| Variable | Default | Notes |
| --- | --- | --- |
| `IRONFLOW_SCHEDULER_INTERVAL_MS` | `1000` | Tick interval |
| `IRONFLOW_SCHEDULER_STALE_SECONDS` | `120` | Stale worker threshold |

Flags: `--interval-ms`, `--stale-seconds`.

## High availability

**Deferred:** multi-replica leader election (Postgres advisory lock / lease row).
Until then, run **exactly one** services container per stack to avoid duplicate ticks.

## Related

- [Docker Compose](docker-compose.md)
- [Env vars](../reference/env-vars.md)
