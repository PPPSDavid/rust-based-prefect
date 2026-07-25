# Quickstart: first deployment

End-to-end path for a **PyPI user** who wants a named deployment, a running API, and a triggered run — similar to Prefect's "first deployable workflow" tutorial.

!!! warning "Prerequisites"
    1. [Installation](INSTALL.md) — `pip install flowoxide-prefect-compat` (Python 3.11 or 3.12).
    2. A terminal where the **`flowoxide`** CLI is on your `PATH` (included in the wheel).
    3. This tutorial uses **`uvicorn`** to start the API (bundled with the package dependencies). The repository's `scripts/flowoxide_server.py` helper is optional and requires a clone.

## Overview

```mermaid
flowchart LR
  A[Write flow file] --> B[Start API]
  B --> C[flowoxide init]
  C --> D[flowoxide deploy]
  D --> E[Trigger run]
```

## 1. Write a flow module

Create a project directory and `flows/hello.py`:

```python
from prefect_compat import flow, task


@task
def greet(name: str) -> str:
    return f"Hello, {name}!"


@flow
def hello_flow(name: str = "FlowOxide") -> str:
    return greet.submit(name).result()
```

## 2. Start the API

In one terminal, from your project directory:

```bash
export FLOWOXIDE_HISTORY_PATH="$(pwd)/data/flowoxide_history.jsonl"
python -m uvicorn prefect_compat.server:app --host 127.0.0.1 --port 8000
```

Check health:

```bash
curl -s http://127.0.0.1:8000/health
```

The embedded local worker and scheduler are enabled by default (`FLOWOXIDE_ENABLE_LOCAL_WORKER=1`, `FLOWOXIDE_ENABLE_SCHEDULER=1`). See [Environment variables](reference/env-vars.md) to disable them for split-process workers.

OpenAPI docs: `http://127.0.0.1:8000/docs`

## 3. Initialize `flowoxide.yaml`

In a second terminal (same project directory):

```bash
flowoxide init
```

Edit the generated manifest so the deployment points at your flow:

```yaml
flowoxide-version: "1"

pull:
  - step: flowoxide.deployments.steps.set_working_directory
    inputs:
      directory: .

deployments:
  - name: hello-deployment
    entrypoint: flows/hello.py:hello_flow
    parameters:
      name: World
    work_pool:
      name: default-process-pool
```

## 4. Deploy

```bash
export FLOWOXIDE_API_URL=http://127.0.0.1:8000
flowoxide deploy --file flowoxide.yaml --name hello-deployment
```

## 5. Trigger a run

```bash
curl -s -X POST "http://127.0.0.1:8000/api/deployments/by-name/hello-deployment/run" \
  -H "Content-Type: application/json" \
  -d '{"parameters": {"name": "Deployment"}}'
```

List flow runs:

```bash
curl -s "http://127.0.0.1:8000/api/flow-runs?limit=5"
```

## 6. Optional: web UI

The Vite UI requires a **repository clone** (`npm --prefix frontend run dev`). PyPI-only users can inspect runs via the REST API or OpenAPI UI at `/docs`.

If you have the repo: [How to run the server and UI](how-to/server-and-ui.md) and [Verify the web UI](ui_e2e_visual_check.md).

## Next steps

- [How to deploy with the CLI and flowoxide.yaml](how-to/deploy-with-cli.md) — manifest schema, `serve`, standalone workers.
- [How to create and update deployments](how-to/deployments.md) — HTTP API recipes.
- [Self-hosted server](SELF_HOSTED_SERVER.md) — workers, schedules, mental model.
- [REST API overview](reference/api.md) — route index.
