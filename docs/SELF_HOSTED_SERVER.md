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
| **Schedule** | Deployments support **interval** (`schedule_interval_seconds`), **cron** (`schedule_cron`), and a Rust-first **RRule subset** (`schedule_rrule`), with shared timing state (`schedule_next_run_at`, `schedule_enabled`). The server maintenance loop evaluates due schedules and enqueues deployment runs. |

For Prefect terminology mapping, see **[Prefect → IronFlow](PREFECT_IRONFLOW_MAPPING.md)**. For exact feature boundaries, **[Compatibility](compatibility.md)** is authoritative.

**Docker:** for a containerized single-server setup, see **[How to run the server in Docker](how-to/docker-quickstart.md)** and **[Secure a self-hosted server](how-to/secure-self-hosted.md)**.

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

### Doctor mode

Run doctor mode from the repository root to print a readiness snapshot for backend dependencies, frontend availability, and Rust library status:

```bash
python scripts/ironflow_server.py doctor
```

Use this before `start` when local setup is uncertain, or after failures to confirm which subsystem needs remediation.

**Manual uvicorn** (equivalent to what the script runs for the API):

```bash
python -m uvicorn python-shim.src.prefect_compat.server:app --host 127.0.0.1 --port 8000
```

The API uses the same **persistence defaults** as in-process flows: JSONL history (e.g. `data/ironflow_history.jsonl` or `IRONFLOW_HISTORY_PATH`) and a SQLite sidecar for queryable state. See the repository README **Persistence defaults** for environment variables.

## 2. What starts with the server

When the FastAPI app loads, it:

1. **Registers** a small set of built-in benchmark flows (`simple_flow`, `wide_flow`, …) and **creates a deployment per flow** (e.g. `simple_flow-local`) with default parameters.
2. Starts a **scheduler thread** (unless disabled) that periodically runs `deployment_maintenance_tick()` — reclaims stale leases, marks stale workers offline, and **fires due interval, cron, or RRule schedules**.
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
- `schedule_cron` (string, mutually exclusive with positive interval and RRule)
- `schedule_rrule` (string, Rust-preferred subset: `FREQ=MINUTELY|HOURLY|DAILY|WEEKLY`, optional positive `INTERVAL`, optional `UNTIL`; no `COUNT`)
- `schedule_next_run_at` (RFC3339 timestamp; optional when the Rust engine can compute the next run)

**Trigger** a run (replace `DEPLOYMENT_ID` with the `id` from the response or list):

```bash
curl -s -X POST http://127.0.0.1:8000/api/deployments/DEPLOYMENT_ID/run \
  -H 'Content-Type: application/json' \
  -d '{"parameters": {"n": 2}}' | python -m json.tool
```

With the default local worker enabled, the deployment run moves from `SCHEDULED` → claimed → **flow execution**; inspect **`GET /api/flow-runs`** and **`GET /api/deployment-runs`** for status.

**Concurrency:** deployments support a **concurrency limit** and **collision strategy** (`ENQUEUE` vs `CANCEL_NEW`) in the data model (see tests in `python-shim/tests/test_deployments_runtime.py`). The HTTP `POST /api/deployments` body in the current server is minimal; advanced policy may require updating the row (maintainers / direct SQLite) until the API grows — see **[Compatibility](compatibility.md)**.

## 4. Schedules (interval + cron + RRule)

Scheduling is enforced inside **`deployment_maintenance_tick`**: when `schedule_enabled` is true and `schedule_next_run_at` is due, the control plane inserts a new deployment run and advances the next tick according to the deployment schedule.

Use **one** schedule type on a deployment: interval, cron, or RRule. The runtime normalizes this by clearing the other schedule fields when one type is selected.

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

RRule schedule ticks run in Rust when the native engine is bound, with a Python fallback for the same narrow deterministic subset: `FREQ=MINUTELY|HOURLY|DAILY|WEEKLY`, optional positive `INTERVAL`, and optional `UNTIL`. Advanced `dateutil`/iCalendar rules and `COUNT` are not implemented yet.

For production-style **external** orchestration (Kubernetes CronJob, systemd timer, CI), the supported pattern is often: call **`POST /api/deployments/{id}/run`** on a timer rather than relying on embedded schedules.

## 5. Standalone worker and CLI (Tier 1)

IronFlow ships a **Tier 1** deployment CLI and manifest format (not full Prefect parity). After installing the shim, the **`ironflow`** entry point provides:

| Command | Purpose |
| --- | --- |
| `ironflow init` | Write a starter **`ironflow.yaml`** if missing. |
| `ironflow deploy` | Create or update deployment(s) from the manifest via the API. |
| `ironflow serve` | Deploy one entry, run pull steps, then execute a local worker loop for that flow. |
| `ironflow worker start` | Poll shared local history for queued deployment runs (standalone process). |

Full examples, manifest schema, and Python **`deploy()`** / **`serve()`** helpers: **[How to deploy with the CLI and `ironflow.yaml`](how-to/deploy-with-cli.md)**.

### Split API and worker (two terminals)

For production-style separation, disable the embedded worker on the server and run a dedicated worker that shares the same **`IRONFLOW_HISTORY_PATH`**:

**Terminal 1 — API + scheduler only:**

```bash
IRONFLOW_ENABLE_LOCAL_WORKER=0 python scripts/ironflow_server.py start --backend-only
```

**Terminal 2 — deploy manifest, then start worker:**

```bash
export IRONFLOW_HISTORY_PATH=data/ironflow_history.jsonl
ironflow deploy --file ironflow.yaml --all
ironflow worker start --file ironflow.yaml --name worker-1 --pool default-process-pool
```

The worker process uses the same JSONL/SQLite persistence as the API; both must agree on **`IRONFLOW_HISTORY_PATH`**. Multiple workers with **distinct `--name`** values can claim from the same pool for horizontal scale (same lease/heartbeat model as the embedded worker).

### Expectations vs Prefect

- **Default dev path:** single process via `ironflow_server.py start` (embedded worker + scheduler).
- **Split path:** `IRONFLOW_ENABLE_LOCAL_WORKER=0` on the API plus `ironflow worker start` or `ironflow serve` / **`serve()`** in Python.
- **Parity:** IronFlow does **not** offer Prefect Cloud work pools, agents, or full YAML/deploy recipe parity — see **[Compatibility](compatibility.md)**.

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
