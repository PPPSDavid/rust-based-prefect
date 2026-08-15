# Prefect concepts → IronFlow

This project is **not** a drop-in replacement for Prefect Cloud or the full Prefect OSS runtime. It is a **prototype** that lets you author flows with **Prefect-like** decorators while execution is governed by IronFlow’s **Rust-first control plane** (`rust-engine`) with a Python compatibility layer (`prefect_compat`). Use this table to orient yourself if you already know Prefect 3.x.

**Upstream Prefect (reference only):** the official [Prefect 3 get started guide](https://docs.prefect.io/v3/get-started) explains flows, tasks, and the mental model this repo echoes. Source for Prefect OSS lives at [github.com/prefecthq/prefect](https://github.com/prefecthq/prefect). IronFlow reuses *patterns*, not the Prefect runtime.

| Prefect (typical mental model) | In IronFlow |
| --- | --- |
| Prefect engine / orchestrator (Python services, workers, …) | **Rust `rust-engine`** owns the deterministic state machine and durable history; Python proposes transitions and runs user task code. Build the `cdylib` and load it from the shim (see README). |
| `from prefect import flow, task` | `from prefect_compat import flow, task` (and `wait`, `set_control_plane`, etc.). Imports come from the **`prefect_compat`** package in this repo, not from `prefect`. |
| Prefect orchestration / API server | Optional HTTP API in `prefect_compat.server` (e.g. `uvicorn python-shim.src.prefect_compat.server:app`). Start with `python scripts/ironflow_server.py start` or run flows **without** any server—orchestration works in-process. Guide: **[Self-hosted server](SELF_HOSTED_SERVER.md)**. |
| Self-hosted Docker / Compose ([server-docker](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-docker), [docker-compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose)) | **Subset:** single-container Tier A ([docker quickstart](how-to/docker-quickstart.md)); compose with Postgres + services + HTTP workers ([docker-compose](how-to/docker-compose.md)). Redis, multi-worker uvicorn, and compose UI image deferred. |
| Secure self-hosted ([security-settings](https://docs.prefect.io/v3/advanced/security-settings)) | **Subset:** `IRONFLOW_*_AUTH_STRING` Basic auth ([secure guide](how-to/secure-self-hosted.md)). No CSRF toggles / Cloud RBAC. |
| Prefect UI | Optional Vite/React app under `frontend/` when you want a local dashboard; run **DAG** tab with **Aggregated fan-out** / **Task runs** views, zoom-pan, and search (see **[DAG and forecast](concepts/dag-and-forecast.md)**). **Concurrency** nav page administers global slot limits. Not the Prefect Cloud UI; not yet a first-class compose service. |
| Deployments, work pools, workers | **Subset:** create/list/trigger deployments, optional **interval, cron, or limited RRule** schedules; **file** or **HTTP** workers (`IRONFLOW_WORKER_MODE`). **UI HTTP subset:** cancel/retry flow runs, process work pools, worker visibility. Not production-parity with Prefect Cloud work pools; schedule/worker hot paths prefer **Rust** when `bind_db` is active. See `COMPATIBILITY.md`. |
| `task.submit()` / futures | Supported: dependency chains, `wait_for`, and concurrent independent submits under `ThreadPoolTaskRunner` or registered children under `ProcessPoolTaskRunner`. Default flow finalization is **`wait_all`** (aggregate child task states in Rust); use `detach=True` or `@flow(final_state="explicit")` for fire-and-forget / body-driven completion. |
| Subflows / nested flows / `run_deployment` | **Two mechanisms (subset):** (1) **inline** — call `child_flow(...)` inside a parent `@flow` (blocking, same process, linked child run); (2) **deployment-backed** — `deployment_ref("name").submit(...).result()` with `SubflowFuture` and `wait_for`. Fire-and-forget uses **`detach=True`**. Not full Prefect subflow / `run_deployment` API parity. Guide: **[How to compose flows with subflows](how-to/subflows.md)**. |
| `task.map()` | Supported with moderate fan-out (see `COMPATIBILITY.md`). |
| Retries, timeouts, cancellation | Enforced at the **control-plane** level for supported flows; semantics are workload-driven—see `COMPATIBILITY.md` for exact boundaries. |
| Task resume / result cache on retry | **Subset (Goal A):** resume-within-lineage skips eligible `COMPLETED` nodes (`None` auto; `@task(persist_result=True)` JSON-safe) when params + inputs match. Not Prefect `cache_policy`; Goal B cross-run cache still open. Guide: **[How to resume tasks and persist results](how-to/task-resume-and-persist.md)**. |
| Cooperative / hard cancel | **Partial:** cancel fences task rows; **`ProcessPoolTaskRunner`** SIGTERM→SIGKILL registered children. Thread-pool bodies need polling (`sleep_cancelable` / `assert_flow_not_cancelled`) or stay best-effort. Guide: **[cancel / pause / resume](how-to/cancel-pause-resume.md)**. |
| `get_run_logger` / `log_prints` | **Partial:** `get_run_logger()` shipped; rows appear in API/UI Logs. `log_prints=` still missing. |
| Runtime context / `prefect.runtime` | **Partial:** `get_run_context()` / `RunContext` exported; not a full Prefect runtime module. |
| Pause / cancel | **Partial:** operator pause `drain`/`terminate` + resume API shipped; process-kill under process runner. UI pause chooser still open. Guide: **[cancel / pause / resume](how-to/cancel-pause-resume.md)**. |
| Artifacts (`create_markdown`, tables, links) | **Partial:** internal `artifact_type=result` rows + GET APIs / UI Artifacts tab. No Prefect user-facing `create_*` artifact API. |
| Variables | **Gap:** no Prefect-style variables JSON store / runtime get. Prefer parameters, env vars, or your own config. |
| Automations / triggers / webhooks | **Gap (design-first):** events + SSE exist; no automation consumers that trigger deployments on state. |
| Deployment concurrency / collision strategy | **Subset:** `concurrency_limit` + `ENQUEUE` / `CANCEL_NEW` on deployments (claim/trigger path). Caps concurrent runs **of that deployment**, not named global slots. |
| Global concurrency limits (`concurrency` / `rate_limit`) | **Subset:** named slots, sync CM, leases, rate-limit decay, HTTP + `ironflow gcl` + UI Concurrency page — **[how-to](how-to/concurrency-limits.md)**. |
| Tag-based concurrency (`@task(tags=...)`) | **Subset:** tags backed by `tag:{name}` limits; gated on enter `Running`. Same how-to. |
| Task caching (`cache_policy`, …) | **Different model:** IronFlow **resume** skips DAG nodes on retry lineage (`None` auto; `@task(persist_result=True)` for JSON-safe values; param + input fingerprints). Not Prefect cache-policy parity. Guide: **[How to resume tasks and persist results](how-to/task-resume-and-persist.md)**. |
| Blocks, integrations, secrets | **Deliberate park** for the MVP; many patterns are unsupported. Use env + optional Basic auth. |
| State hooks (`on_running`, …) | IronFlow uses **`transition_hooks`** on `@flow` / `@task` with `TransitionHookSpec` / `on_transition`—see `COMPATIBILITY.md`. This is an **extension**, not Prefect’s hook API. |
| Event stream / observability | Local persistence (JSONL + SQLite) and optional API/SSE; see README **History persistence**. Automations on those events are not supported. |
| Static DAG / compile-time insights | `static-planner/` analyzes `@flow` bodies (`submit`, `map`, `wait_for`, repeated tasks, `@task(name=...)`) and stores a per-run manifest + forecast. See **[DAG and forecast](concepts/dag-and-forecast.md)**. Dynamic regions fall back to runtime-inferred DAGs. |
| Run DAG UI | Local UI **DAG** tab: **Aggregated fan-out** (planned graph, fan-out collapsed) vs **Task runs**; dependencies always left→right, parallel top→bottom; zoom/pan, search, path highlight. API: `mode=logical|expanded`. |

## Practical “bring your own tasks” path

1. **Clone** the repo, **checkout a [release tag](https://github.com/PPPSDavid/rust-based-prefect/releases)** (for example `v0.1.2`) when you want a stable baseline, then create the conda env from `environment.yml` (or install `requirements-ci.txt` in a venv). Alternatively install only `prefect_compat` with pip from git — see the root README *Using a numbered release*.
2. **Port imports**: replace `prefect` flow/task imports with `prefect_compat` (and wire `set_control_plane` / `InMemoryControlPlane` as in tests under `python-shim/tests/`).
3. **Stay inside the subset**: prefer `submit` chains, `map` with clear static shape, **supported subflows** ([how-to](how-to/subflows.md)), and control-plane features listed in `COMPATIBILITY.md`.
4. **Validate**: run `python -m pytest python-shim/tests` and your own scripts locally; add a small script under `scripts/` if you want a repeatable smoke test.
5. **Optional UI/API**: start `scripts/ironflow_server.py` to inspect runs that were persisted to disk — nested runs show parent/child links and DAG node kinds `inline_subflow` / `subflow_task`.

When something behaves differently from Prefect, **`COMPATIBILITY.md`** is the source of truth for what is intentional versus not yet implemented.
