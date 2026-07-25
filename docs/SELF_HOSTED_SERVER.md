# Self-hosted server (API, workers, deployments)

This guide is the **FlowOxide** counterpart to Prefect’s self-hosted walkthroughs: how to run the **optional HTTP API**, what **workers** and **deployments** mean here, and how **scheduling** fits in. It assumes you already completed **[Installation](INSTALL.md)** (clone, Python env, `cargo build`).

If you only need a minimal in-process flow with no network stack, use **[Quick start (demo flow)](QUICKSTART_DEMO.md)** first.

**Prefect docs to borrow / enhance from** (operator shape and page structure — FlowOxide remains a subset; see **[Compatibility](compatibility.md)**):

| Prefect guide | FlowOxide counterpart |
| --- | --- |
| [Local server (CLI)](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli) | This page + `scripts/flowoxide_server.py` / uvicorn |
| [Server in Docker](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-docker) | **[Docker quickstart](how-to/docker-quickstart.md)** |
| [Docker Compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose) | **[Docker Compose](how-to/docker-compose.md)** (Postgres + services + HTTP worker; Redis deferred) |
| [Secure self-hosted](https://docs.prefect.io/v3/advanced/security-settings) | **[Secure a self-hosted server](how-to/secure-self-hosted.md)** (Basic auth; CSRF deferred) |

## Mental model

| Piece | In FlowOxide today |
| --- | --- |
| **Orchestration kernel** | **`rust-engine`** — deterministic state machine and history; Python calls into it via `prefect_compat`. |
| **“Server”** | A **FastAPI** app: `prefect_compat.server` (uvicorn). It exposes REST endpoints for runs, deployments, and streams. It is **not** the Prefect OSS API or Prefect Cloud. |
| **Worker** | A process (or thread) that **claims** queued **deployment runs** and executes the referenced `@flow`. Dev default: **in-process** worker. Production compose: **HTTP** worker (`FLOWOXIDE_WORKER_MODE=http`). |
| **Background services** | Schedule ticks and lease reclaim — embedded in the API for local dev, or `flowoxide server services start` when embeds are off (compose). |
| **Deployment** | A **named** binding: flow name, optional `module:function` **entrypoint**, default parameters, pause flag. Stored in the control plane (SQLite or Postgres). |
| **Schedule** | Deployments support **interval** (`schedule_interval_seconds`), **cron** (`schedule_cron`), and a Rust-first **RRule subset** (`schedule_rrule`), with shared timing state (`schedule_next_run_at`, `schedule_enabled`). Maintenance evaluates due schedules and enqueues deployment runs. |

For Prefect terminology mapping, see **[Prefect → FlowOxide](PREFECT_FLOWOXIDE_MAPPING.md)**. For exact feature boundaries, **[Compatibility](compatibility.md)** is authoritative.

## Deployment shapes (pick one)

| Shape | When to use | Guide |
| --- | --- | --- |
| **Local process** | Day-to-day development; SQLite/JSONL; embedded worker + scheduler | Sections below |
| **Single Docker container** | One-box demo / small deploy; file persistence volume | [Docker quickstart](how-to/docker-quickstart.md) |
| **Docker Compose** | Production-shaped: Postgres + API + services + HTTP workers | [Docker Compose](how-to/docker-compose.md), [Postgres](how-to/database-postgres.md), [HTTP workers](how-to/worker-http-mode.md), [background services](how-to/run-background-services.md) |
| **+ Basic auth** | Any shared network | [Secure self-hosted](how-to/secure-self-hosted.md) |

## 1. Start the API (and optional UI)

From the **repository root**, with dependencies installed as in the README:

```bash
python scripts/flowoxide_server.py start
```

Typical URLs:

- **API:** `http://127.0.0.1:8000` — try `GET /health`
- **UI:** `http://localhost:4173` (Vite dev server; requires Node/npm)

**Backend only** (no frontend):

```bash
python scripts/flowoxide_server.py start --backend-only
```

### Doctor mode

Run doctor mode from the repository root to print a readiness snapshot for backend dependencies, frontend availability, and Rust library status:

```bash
python scripts/flowoxide_server.py doctor
```

Use this before `start` when local setup is uncertain, or after failures to confirm which subsystem needs remediation.

**Manual uvicorn** (equivalent to what the script runs for the API):

```bash
python -m uvicorn python-shim.src.prefect_compat.server:app --host 127.0.0.1 --port 8000
```

The API uses the same **persistence defaults** as in-process flows: JSONL history (e.g. `data/flowoxide_history.jsonl` or `FLOWOXIDE_HISTORY_PATH`) and a SQLite sidecar for queryable state. For Postgres, set `FLOWOXIDE_DATABASE_URL` — see **[How to use Postgres](how-to/database-postgres.md)**. Full env list: **[Environment variables](reference/env-vars.md)**.

## 2. What starts with the server

When the FastAPI app loads, it:

1. **Registers** a small set of built-in benchmark flows (`simple_flow`, `wide_flow`, …) and **creates a deployment per flow** (e.g. `simple_flow-local`) with default parameters.
2. Starts a **scheduler thread** (unless disabled) that periodically runs `deployment_maintenance_tick()` — reclaims stale leases, marks stale workers offline, and **fires due interval, cron, or RRule schedules**.
3. Starts a **local worker thread** (unless disabled) that repeatedly **claims** the next `SCHEDULED` deployment run and runs the flow **in that process**.

So a single `flowoxide_server.py start` gives you API + **embedded worker + scheduler** for local development. This is a deliberate **single-process** convenience — not the only model. For Prefect-shaped multi-process / Compose layouts, disable the embeds and use [background services](how-to/run-background-services.md) + [HTTP workers](how-to/worker-http-mode.md).

### Environment toggles (local worker / scheduler)

| Variable | Default | Meaning |
| --- | --- | --- |
| `FLOWOXIDE_ENABLE_LOCAL_WORKER` | `1` | Set to `0`, `false`, or `no` to **disable** the in-process worker loop (API only; runs stay queued until something else claims them). |
| `FLOWOXIDE_ENABLE_SCHEDULER` | `1` | Set to `0`, `false`, or `no` to **disable** the maintenance thread (no periodic schedule ticks or related maintenance from this process). |
| `FLOWOXIDE_LOCAL_WORKER_NAME` | `local-worker-1` | Worker name recorded when claiming runs and sending heartbeats. |

Example — API only, no embedded worker (for experiments or a separate claimant):

```bash
FLOWOXIDE_ENABLE_LOCAL_WORKER=0 python scripts/flowoxide_server.py start --backend-only
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

FlowOxide ships a **Tier 1** deployment CLI and manifest format (not full Prefect parity). After installing the shim, the **`flowoxide`** entry point provides:

| Command | Purpose |
| --- | --- |
| `flowoxide init` | Write a starter **`flowoxide.yaml`** if missing. |
| `flowoxide deploy` | Create or update deployment(s) from the manifest via the API. |
| `flowoxide serve` | Deploy one entry, run pull steps, then execute a local worker loop for that flow. |
| `flowoxide worker start` | Claim deployment runs — default **`file`** mode (shared history/SQLite) or **`--worker-mode http`** / `FLOWOXIDE_WORKER_MODE=http` (API claim only). |

Full examples, manifest schema, and Python **`deploy()`** / **`serve()`** helpers: **[How to deploy with the CLI and `flowoxide.yaml`](how-to/deploy-with-cli.md)**.

### Split API and worker (two terminals)

**Dev / single-host (`file` mode):** disable the embedded worker and share **`FLOWOXIDE_HISTORY_PATH`**:

**Terminal 1 — API + scheduler only:**

```bash
FLOWOXIDE_ENABLE_LOCAL_WORKER=0 python scripts/flowoxide_server.py start --backend-only
```

**Terminal 2 — deploy manifest, then start worker:**

```bash
export FLOWOXIDE_HISTORY_PATH=data/flowoxide_history.jsonl
flowoxide deploy --file flowoxide.yaml --all
flowoxide worker start --file flowoxide.yaml --name worker-1 --pool default-process-pool
```

Both processes must agree on **`FLOWOXIDE_HISTORY_PATH`**. Multiple workers with **distinct `--name`** values can claim from the same pool.

**Production-shaped (HTTP / Compose):** disable embeds on the API, run [background services](how-to/run-background-services.md), and start workers with `FLOWOXIDE_WORKER_MODE=http` (no shared DB volume) — see **[Docker Compose](how-to/docker-compose.md)** and **[HTTP workers](how-to/worker-http-mode.md)**.

### Expectations vs Prefect

- **Default dev path:** single process via `flowoxide_server.py start` (embedded worker + scheduler) — analogous to Prefect’s local [server CLI](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli) simplicity, not its full feature set.
- **Split / compose path:** disable embeds on the API (`FLOWOXIDE_ENABLE_LOCAL_WORKER=0`, `FLOWOXIDE_ENABLE_SCHEDULER=0`), run **[background services](how-to/run-background-services.md)**, and use **[HTTP workers](how-to/worker-http-mode.md)** — shaped like Prefect [Docker Compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose) (Postgres + worker), without Redis or multi-worker uvicorn yet.
- **Parity:** FlowOxide does **not** offer Prefect Cloud work pools, agents, Redis messaging, CSRF toggles, or full YAML/deploy recipe parity — see **[Compatibility](compatibility.md)**.

## 6. Related endpoints and UI

Useful for debugging:

- `GET /api/flow-runs`, `GET /api/flow-runs/{id}`
- `GET /api/deployment-runs`
- `GET /history/summary`
- SSE: `GET /api/stream/flow-runs` (lightweight polling stream for the optional UI)

Optional UI walkthrough: **[Optional: verify the web UI](ui_e2e_visual_check.md)**.

## 7. Next steps

- **[Docker Compose](how-to/docker-compose.md)** — production-shaped stack.
- **[Quick start (demo flow)](QUICKSTART_DEMO.md)** — minimal `@flow` without a server.
- **[Architecture](architecture.md)** — Python ↔ Rust data path.
- **[Compatibility](compatibility.md)** — what is implemented vs stubbed for deployments and scheduling.
