# How to deploy with the CLI and `ironflow.yaml`

IronFlow **Tier 1** adds a Prefect-style deployment surface: an **`ironflow.yaml`** manifest, the **`ironflow`** CLI (`init`, `deploy`, `serve`, `worker start`), and Python helpers **`deploy()`** / **`serve()`**. This guide covers the supported subset — not full Prefect Cloud or work-pool parity.

**Prerequisites:** [Installation](../INSTALL.md) and a running API (see [How to run the server and UI](server-and-ui.md)). From the repository root, the CLI is available after installing the shim (`pip install -e python-shim` or `requirements-ci.txt`).

## `ironflow.yaml` schema (Tier 1 subset)

Top-level fields:

| Field | Required | Description |
| --- | --- | --- |
| `ironflow-version` | yes | Manifest version string (currently `"1"`). |
| `pull` | no | List of pull steps run before importing flow code (`serve` / `worker start --file`). |
| `deployments` | yes | One or more deployment definitions (see below). |

Each deployment entry supports:

| Field | Required | Description |
| --- | --- | --- |
| `name` | yes | Unique deployment name in the control plane. |
| `entrypoint` | usually | `path/to/module.py:flow_fn` or `package.module:flow_fn`. Normalized to a dotted module path. |
| `flow_name` | alternative | Registered flow name (e.g. server built-ins like `simple_flow`) when no `entrypoint` is set. |
| `parameters` | no | Default run parameters (maps to `default_parameters` in the API). |
| `work_pool` | no | `{ name: <pool-name> }` — defaults to `default-process-pool`. |
| `schedule` | no | `{ cron, interval, rrule, enabled }` — interval/cron/RRule are mutually exclusive (same rules as the HTTP API). |
| `paused` | no | When `true`, the deployment does not enqueue scheduled runs. |
| `concurrency_limit` | no | Max concurrent deployment runs (optional). |
| `collision_strategy` | no | `ENQUEUE` or `CANCEL_NEW` when a concurrency limit is set. |

**Pull steps (Tier 1):**

| Step | Inputs |
| --- | --- |
| `ironflow.deployments.steps.set_working_directory` | `directory` — `chdir` before flow import |

### Example manifest

```yaml
ironflow-version: "1"

pull:
  - step: ironflow.deployments.steps.set_working_directory
    inputs:
      directory: .

deployments:
  - name: my-deployment
    entrypoint: flows/example.py:my_flow
    parameters:
      n: 4
    work_pool:
      name: default-process-pool
    schedule:
      cron: "0 * * * *"
```

Generate this template with **`ironflow init`** (see below).

## CLI commands

All commands accept **`--api-url`** (default: `IRONFLOW_API_URL` or `http://127.0.0.1:8000`).

### `ironflow init`

Write **`ironflow.yaml`** if it does not already exist.

```bash
ironflow init
ironflow init --directory ./deploy
ironflow init --recipe process --directory .
```

### `ironflow deploy`

Load the manifest and **create or update** deployment(s) via the HTTP API.

```bash
# Single deployment (required when the manifest lists more than one)
ironflow deploy --file ironflow.yaml --name my-deployment

# All deployments in the manifest
ironflow deploy --file ironflow.yaml --all

# Preview without mutating the API
ironflow deploy --file ironflow.yaml --all --dry-run

# Remote API
ironflow deploy --all --api-url http://127.0.0.1:8000
```

### `ironflow serve`

Deploy **one** deployment, run manifest **pull** steps, then start a **local worker loop** that executes that flow in-process. Combines `deploy` + worker for a single deployment (similar in spirit to Prefect `serve()`).

```bash
ironflow serve --file ironflow.yaml --name my-deployment
ironflow serve --name my-deployment --pool default-process-pool
ironflow serve --name my-deployment --worker-name prod-worker-1
```

When the API server also runs an embedded worker, disable it so only the `serve` process claims runs (see two-terminal workflow below).

### `ironflow worker start`

Run a **standalone worker** that polls shared local history (`IRONFLOW_HISTORY_PATH`) for queued deployment runs. Optionally run pull steps from a manifest first.

```bash
ironflow worker start --pool default-process-pool
ironflow worker start --name worker-1 --lease-seconds 30
ironflow worker start --file ironflow.yaml
IRONFLOW_HISTORY_PATH=data/ironflow_history.jsonl ironflow worker start
```

## Two-terminal workflow (API + standalone worker)

Split the **control plane** (API + scheduler) from **execution** (worker process). Both processes must share the same persistence directory.

**Terminal 1 — API only (no embedded worker):**

```bash
export IRONFLOW_ENABLE_LOCAL_WORKER=0
python scripts/ironflow_server.py start --backend-only
```

**Terminal 2 — register deployments, then run a worker:**

```bash
export IRONFLOW_HISTORY_PATH=data/ironflow_history.jsonl
export IRONFLOW_API_URL=http://127.0.0.1:8000

ironflow deploy --file ironflow.yaml --all
ironflow worker start --file ironflow.yaml --name worker-1 --pool default-process-pool
```

Trigger a run from the API or UI; the standalone worker claims and executes it. Keep **`IRONFLOW_HISTORY_PATH`** identical on both terminals (default: `data/ironflow_history.jsonl`).

| Variable | Role |
| --- | --- |
| `IRONFLOW_ENABLE_LOCAL_WORKER=0` | Disables the in-process worker in the API server. |
| `IRONFLOW_HISTORY_PATH` | Shared JSONL + SQLite sidecar path for server and worker. |
| `IRONFLOW_API_URL` | Base URL for `deploy` / pool resolution (worker uses local history for claims). |

## Python API: `deploy()` and `serve()`

Import from **`prefect_compat.deploy`** (same behavior as the CLI, callable from scripts or notebooks).

### `deploy()`

Upsert a deployment. Pass a **`@flow`** function or an explicit **`entrypoint`**.

```python
from prefect_compat import flow
from prefect_compat.deploy import deploy

@flow
def my_flow(n: int = 1) -> int:
    return n + 1

result = deploy(
    my_flow,
    name="my-deployment",
    parameters={"n": 4},
    work_pool_name="default-process-pool",
    api_url="http://127.0.0.1:8000",
)
print(result["action"], result["deployment"]["id"])

# Update defaults on a later call (same name → update)
deploy(my_flow, name="my-deployment", parameters={"n": 99})

# Preview only
deploy(my_flow, name="my-deployment", dry_run=True)
```

Optional schedule kwargs: `schedule_cron`, `schedule_interval_seconds`, `schedule_rrule`.

### `serve()`

Deploy the flow, optionally run **pull steps**, then block in a **worker loop** for that flow.

```python
import threading

from prefect_compat import flow
from prefect_compat.deploy import serve
from prefect_compat.deploy.spec import PullStepSpec

@flow
def my_flow(n: int = 1) -> int:
    return n + 1

stop = threading.Event()
serve(
    my_flow,
    name="my-deployment",
    pull_steps=[
        PullStepSpec(
            step="ironflow.deployments.steps.set_working_directory",
            inputs={"directory": "."},
        )
    ],
    api_url="http://127.0.0.1:8000",
    worker_name="serve-my-deployment",
    stop_event=stop,  # set stop.set() from another thread to exit
)
```

Run with **`IRONFLOW_ENABLE_LOCAL_WORKER=0`** on the API server when using `serve()` or `worker start` against the same deployment queue.

## Related guides

- [How to create and update deployments](deployments.md) — HTTP API for create/patch/trigger.
- [Self-hosted server](../SELF_HOSTED_SERVER.md) — mental model, scheduler, worker split.
- [Compatibility matrix](../compatibility.md) — Tier 1 CLI/YAML limits vs Prefect.
