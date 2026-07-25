# How to run IronFlow with Docker Compose

Production-shaped stack: **Postgres**, **API server** (no embedded worker/scheduler),
**background services**, and **HTTP worker(s)**. Workers never share a filesystem
with the server.

**Prefect example to borrow from:** [Prefect Server via Docker Compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose) — Postgres + API (`--no-services`) + background services + worker. IronFlow mirrors that topology; **Redis** and a bundled UI service are intentionally deferred (see [Compatibility](../compatibility.md)).

For a single-container quickstart, see [Docker quickstart](docker-quickstart.md).

## Prerequisites

- Docker + Compose v2
- For local builds: Python + Rust toolchain to produce `dist/wheels/*.whl`

## Start the stack (local wheels)

```bash
mkdir -p dist/wheels
python -m pip install build
python -m build --wheel --outdir dist/wheels --directory python-shim

docker compose -f deploy/docker/compose.yml up --build
```

Health: `http://127.0.0.1:8000/health`.

| Service | Role |
| --- | --- |
| `postgres` | Control-plane DB (`IRONFLOW_DATABASE_URL`) |
| `server` | FastAPI; `IRONFLOW_ENABLE_SCHEDULER=0`, `IRONFLOW_ENABLE_LOCAL_WORKER=0` |
| `services` | `ironflow server services start` (schedule / lease reclaim) |
| `worker` | `IRONFLOW_WORKER_MODE=http` → claim API |

Optional auth overlay:

```bash
docker compose -f deploy/docker/compose.yml -f deploy/docker/compose.auth.yml up --build
```

## Smoke test

```bash
bash scripts/docker_compose_smoke.sh
```

## Operations notes

- Run **one** `services` replica until HA leader election lands (see [background services](run-background-services.md)).
- Scale workers horizontally; keep them on `http` mode and the same `IRONFLOW_API_URL`.
- Redis is **not** required for this stack (deferred).
- UI image is optional / separate from this compose file.

## Related

- [HTTP workers](worker-http-mode.md)
- [Postgres](database-postgres.md)
- [Background services](run-background-services.md)
- [Env vars](../reference/env-vars.md)
