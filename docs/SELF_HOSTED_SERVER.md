# Self-hosted server (API, workers, deployments)

This guide is the **IronFlow** counterpart to Prefect’s [self-hosted server / CLI](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli) walkthrough: how to run the **optional HTTP API**, what **workers** and **deployments** mean here, and how **scheduling** fits in. It assumes you already completed **[Installation](INSTALL.md)** (clone, Python env, `cargo build`).

If you only need a minimal in-process flow with no network stack, use **[Quick start (demo flow)](QUICKSTART_DEMO.md)** first.

## Mental model

| Piece | In IronFlow today |
| --- | --- |
| **Orchestration kernel** | **`rust-engine`** — deterministic state machine and history; Python calls into it via `prefect_compat`. |
| **“Server”** | A **FastAPI** app: `prefect_compat.server` (uvicorn). It exposes REST endpoints for runs, deployments, and streams. It is **not** the Prefect OSS API or Prefect Cloud. |
| **Worker** | A process (or thread) that **claims** queued **deployment runs** and executes the referenced `@flow`. The bundled server starts a **local in-process worker** by default. |
| **Deployment** | A **named** binding: flow name, optional `module:function` **entrypoint**, default parameters, pause flag. Stored in the control plane (SQLite read model beside JSONL history). |
| **Schedule** | Deployments support **interval** (`schedule_interval_seconds`) and **cron** (`schedule_cron`) schedules, with shared timing state (`schedule_next_run_at`, `schedule_enabled`). The server maintenance loop evaluates due schedules and enqueues deployment runs. |

For Prefect terminology mapping, see **[Prefect → IronFlow](PREFECT_IRONFLOW_MAPPING.md)**. For exact feature boundaries, **[Compatibility](compatibility.md)** is authoritative.

## 1. Start the API (and optional UI)

From the **repository root**, with dependencies installed as in the README:

```bash
python scripts/ironflow_server.py start
```

Typical URLs:

- **API:** `http://127.0.0.1:8000` — try `GET /health`
- **UI:** `http://localhost:4173` (Vite dev server; requires Node/npm)

**Backend only** (no frontend):

```bash
python scripts/ironflow_server.py start --backend-only
```

**Manual uvicorn** (equivalent to what the script runs for the API):

```bash
python -m uvicorn python-shim.src.prefect_compat.server:app --host 127.0.0.1 --port 8000
```

The API uses the same **persistence defaults** as in-process flows: JSONL history (e.g. `data/ironflow_history.jsonl` or `IRONFLOW_HISTORY_PATH`) and a SQLite sidecar for queryable state. See the repository README **Persistence defaults** for environment variables.

## 2. What starts with the server

When the FastAPI app loads, it:

1. **Registers** a small set of built-in benchmark flows (`simple_flow`, `wide_flow`, …) and **creates a deployment per flow** (e.g. `simple_flow-local`) with default parameters.
2. Starts a **scheduler thread** (unless disabled) that periodically runs `deployment_maintenance_tick()` — reclaims stale leases, marks stale workers offline, and **fires due interval or cron schedules**.
3. Starts a **local worker thread** (unless disabled) that repeatedly **claims** the next `SCHEDULED` deployment run and runs the flow **in that process**.

So a single `ironflow_server.py start` gives you API + **embedded worker + scheduler** for local development. This is **not** the same as Prefect’s separate `prefect worker` process model; it is a deliberate **single-process** convenience for the MVP.

### Environment toggles (local worker / scheduler)

| Variable | Default | Meaning |
| --- | --- | --- |
| `IRONFLOW_ENABLE_LOCAL_WORKER` | `1` | Set to `0`, `false`, or `no` to **disable** the in-process worker loop (API only; runs stay queued until something else claims them). |
| `IRONFLOW_ENABLE_SCHEDULER` | `1` | Set to `0`, `false`, or `no` to **disable** the maintenance thread (no periodic schedule ticks or related maintenance from this process). |
| `IRONFLOW_LOCAL_WORKER_NAME` | `local-worker-1` | Worker name recorded when claiming runs and sending heartbeats. |

Example — API only, no embedded worker (for experiments or a separate claimant):

```bash
IRONFLOW_ENABLE_LOCAL_WORKER=0 python scripts/ironflow_server.py start --backend-only
```

## 3. Deployments and triggering a run

**List** deployments:

```bash
curl -s http://127.0.0.1:8000/api/deployments | python -m json.tool
```

**Create** a deployment that points at a registered flow name (or supply `entrypoint` for a `module:function` elsewhere on `PYTHONPATH`):

```bash
curl -s -X POST http://127.0.0.1:8000/api/deployments \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "my-simple",
    "flow_name": "simple_flow",
    "default_parameters": {"n": 4},
    "paused": false,
    "schedule_enabled": true,
    "schedule_interval_seconds": 300
  }' | python -m json.tool
```

`POST /api/deployments` also accepts schedule fields:

- `schedule_enabled` (bool)
- `schedule_interval_seconds` (int, > 0)
- `schedule_cron` (string, mutually exclusive with positive interval)
- `schedule_next_run_at` (RFC3339 timestamp; optional when the Rust engine can compute the next run)

**Trigger** a run (replace `DEPLOYMENT_ID` with the `id` from the response or list):

```bash
curl -s -X POST http://127.0.0.1:8000/api/deployments/DEPLOYMENT_ID/run \
  -H 'Content-Type: application/json' \
  -d '{"parameters": {"n": 2}}' | python -m json.tool
```

With the default local worker enabled, the deployment run moves from `SCHEDULED` → claimed → **flow execution**; inspect **`GET /api/flow-runs`** and **`GET /api/deployment-runs`** for status.

**Concurrency:** deployments support a **concurrency limit** and **collision strategy** (`ENQUEUE` vs `CANCEL_NEW`) in the data model (see tests in `python-shim/tests/test_deployments_runtime.py`). The HTTP `POST /api/deployments` body in the current server is minimal; advanced policy may require updating the row (maintainers / direct SQLite) until the API grows — see **[Compatibility](compatibility.md)**.

## 4. Schedules (interval + cron)

Scheduling is enforced inside **`deployment_maintenance_tick`**: when `schedule_enabled` is true and `schedule_next_run_at` is due, the control plane inserts a new deployment run and advances the next tick according to the deployment schedule.

Use **either** interval or cron on a deployment (not both). The runtime normalizes this by clearing `schedule_cron` when a positive interval is set, or clearing interval when cron is set.

You can also patch scheduling after creation:

```bash
curl -s -X PATCH http://127.0.0.1:8000/api/deployments/DEPLOYMENT_ID \
  -H 'Content-Type: application/json' \
  -d '{
    "schedule_enabled": true,
    "schedule_cron": "*/10 * * * *"
  }' | python -m json.tool
```

When running without the Rust engine (`bind_db` unavailable), cron schedules require `schedule_next_run_at` to be provided explicitly. With Rust enabled, the control plane computes the next cron tick.

For production-style **external** orchestration (Kubernetes CronJob, systemd timer, CI), the supported pattern is often: call **`POST /api/deployments/{id}/run`** on a timer rather than relying on embedded schedules.

## 5. Workers in production (expectations)

- **Today:** the reference path is the **embedded** worker thread in `prefect_compat.server`, plus **heartbeats** under `IRONFLOW_LOCAL_WORKER_NAME`.
- **Scaling out:** multiple **separate processes** each calling `claim_next_deployment_run` with **distinct worker names** is the intended direction for horizontal scale; there is **no** separate `ironflow worker` CLI packaged like Prefect’s worker yet.
- **Parity:** IronFlow does **not** offer Prefect work pools, agents, or Cloud-grade worker isolation — see **[Compatibility](compatibility.md)**.

## 6. Related endpoints and UI

Useful for debugging:

- `GET /api/flow-runs`, `GET /api/flow-runs/{id}`
- `GET /api/deployment-runs`
- `GET /history/summary`
- SSE: `GET /api/stream/flow-runs` (lightweight polling stream for the optional UI)

Optional UI walkthrough: **[Optional: verify the web UI](ui_e2e_visual_check.md)**.

## 7. Next steps

- **[Quick start (demo flow)](QUICKSTART_DEMO.md)** — minimal `@flow` without a server.
- **[Architecture](architecture.md)** — Python ↔ Rust data path.
- **[Compatibility](compatibility.md)** — what is implemented vs stubbed for deployments and scheduling.
