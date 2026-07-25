# How-to guides

Task-focused guides for common goals. For a minimal first run, use **[Get started → Installation](../INSTALL.md)** and **[Quick start (demo flow)](../QUICKSTART_DEMO.md)**. For API, workers, and deployments in depth, see **[Self-hosted server](../SELF_HOSTED_SERVER.md)** — including Docker / Compose shapes mapped against Prefect’s [self-hosted how-tos](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli).

- **[How to set up IronFlow](setup.md)** — clone, Python environment, build the Rust engine, environment variables, `PYTHONPATH`.
- **[How to run the server and UI](server-and-ui.md)** — API + optional Vite UI via `scripts/ironflow_server.py`.
- **[How to run the server in Docker](docker-quickstart.md)** — single-container image, volumes, optional basic auth.
- **[How to run IronFlow with Docker Compose](docker-compose.md)** — Postgres + API + services + HTTP workers.
- **[How to run background services](run-background-services.md)** — `ironflow server services start` without an HTTP listener.
- **[How to use Postgres for the control plane](database-postgres.md)** — `IRONFLOW_DATABASE_URL`, Rust claim bind.
- **[How to run workers in HTTP mode](worker-http-mode.md)** — `IRONFLOW_WORKER_MODE=http`, claim API, no shared DB volume.
- **[How to secure a self-hosted server](secure-self-hosted.md)** — `IRONFLOW_*_AUTH_STRING` Basic auth and reverse-proxy notes.
- **[How to deploy with the CLI and `ironflow.yaml`](deploy-with-cli.md)** — `ironflow init` / `deploy` / `serve` / `worker start`, manifest schema, and `deploy()` / `serve()` Python API.
- **[How to create and update deployments](deployments.md)** — create/patch deployments, trigger runs, and configure interval or cron schedules.
- **[How to compose flows with subflows](subflows.md)** — inline blocking children vs `deployment_ref(...).submit()`, `wait_for`, fire-and-forget, and UI navigation.
- **[How to choose a task runner](choose-task-runners.md)** — thread vs process vs sequential for `submit` / `map`; API/remote vs local CPU workloads.
- **[How to use concurrency limits](concurrency-limits.md)** — global slots, `concurrency` / `rate_limit`, tag-based task caps.
- **[How to resume tasks and persist results](task-resume-and-persist.md)** — DAG resume on retry, `@task(persist_result=True)`, JSON allowlist, UI.
- **[How to cancel, pause, and resume](cancel-pause-resume.md)** — drain vs terminate pause, process-kill cancel, resume + P1 skip/recompute.
- **[How to port a flow from Prefect](port-from-prefect.md)** — imports, control plane, staying inside the supported subset.

Conceptual background: **[Concepts overview](../concepts/index.md)**. Normative limits: **[Compatibility matrix](../compatibility.md)**.
