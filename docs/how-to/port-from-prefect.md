# How to port a flow from Prefect

IronFlow is **subset-compatible** with Prefect 3.x patterns, not a drop-in replacement for the full Prefect OSS or Cloud runtime. Use this checklist when moving an existing flow.

1. **Swap imports** — Replace `from prefect import flow, task` (and related helpers) with **`from prefect_compat import ...`**. See **[Prefect → IronFlow](../PREFECT_IRONFLOW_MAPPING.md)** for a full concept map.
2. **Wire the control plane** — Register an **`InMemoryControlPlane`** (or the persistence-backed plane your app uses) with **`set_control_plane`** before running flows, matching patterns in **`python-shim/tests/`** and **[Quick start (demo flow)](../QUICKSTART_DEMO.md)**.
3. **Stay inside the supported subset** — Prefer **`submit`** chains, **`map`** with clear shape, retries/timeouts/cancellation as documented. For nesting, use IronFlow’s **two subflow mechanisms** ([how-to](subflows.md)) instead of Prefect-only APIs. For expensive tasks you want skipped on flow-run **retry**, use **`@task(persist_result=True)`** (JSON-safe values) or return **`None`** (auto marker) in **static-effective** flows — see **[How to resume tasks and persist results](task-resume-and-persist.md)** and **[graph mode and retry](graph-mode-and-retry.md)**. Do not expect Prefect `cache_policy` / cross-flow caching. Avoid relying on blocks, full deployment/work-pool parity, or Prefect Cloud–only features until **[Compatibility matrix](../compatibility.md)** says otherwise.
4. **Rename hooks if you use them** — Prefect’s named lifecycle hooks are not mirrored literally. Use IronFlow **`transition_hooks`** with **`TransitionHookSpec`** / **`on_transition`**; see **[Flows](../concepts/flows.md)** and the compatibility matrix.
5. **Map Prefect caching carefully** — Prefect’s default cache key (inputs + task source + flow run) is **not** implemented. IronFlow resume is DAG-slot + lineage based. Guide: **[task resume and persist](task-resume-and-persist.md)**.
6. **Validate** — Run **`python -m pytest python-shim/tests`** and your own scripts locally; add a small smoke script under `scripts/` if you want a repeatable check.

## Common Prefect APIs → IronFlow status

Use this table when a Prefect import fails or behaves differently. Status values: **supported** (use the IronFlow equivalent), **partial** (subset / rename), **unsupported** (rewrite or wait), **deliberate** (intentional divergence — do not expect Prefect parity).

| Prefect import / API | IronFlow status | What to do |
| --- | --- | --- |
| `from prefect import flow, task` | supported | `from prefect_compat import flow, task` |
| `task.submit` / `wait_for` / `wait` | supported | Same patterns; default flow finalization is **`wait_all`** (see matrix) |
| `task.map` | supported | Moderate fan-out; pick a runner via [how-to](choose-task-runners.md) |
| `@task(retries=…)` / `retry_delay_seconds` / `timeout_seconds` | unsupported | Decorators do not accept Prefect retry/timeout kwargs; spec for future in-run retry: `docs/plans/task-auto-retry.md`. Flow-run **cancel + deployment retry** exist via API/UI |
| `@flow(graph_mode=…)` / static retry contract | deliberate | IronFlow **`auto`/`static`/`dynamic`** — [graph mode and retry](graph-mode-and-retry.md). Prefect has no equivalent |
| `get_run_logger` / `log_prints=` | partial | `from prefect_compat import get_run_logger` writes to the run log store + UI Logs tab. `log_prints=` not supported yet; use the logger instead of bare `print` |
| `on_running` / `on_failure` / … hooks | deliberate | Map notify logic to `transition_hooks=` + `on_transition(...)` that return `None`. To change the recorded terminal, return a `RunState` or `TransitionDecision` (IronFlow extension; not Prefect hook behavior). |
| `run_deployment` / nested-flow helpers | partial | Use `deployment_ref(...).submit()` or inline `child_flow(...)` — [subflows](subflows.md) |
| Blocks (`prefect.blocks.*`) | deliberate | Not in scope; use env vars / your own config |
| `cache_policy` / task result cache | unsupported | Different model: lineage resume + `@task(persist_result=True)` / `None` markers — [task resume](task-resume-and-persist.md). Not Prefect `cache_policy` |
| Variables (`prefect.variables`) | unsupported | Pass parameters or read env / files |
| Secrets / profiles / settings | unsupported | Env vars + optional [Basic auth](secure-self-hosted.md) |
| `prefect.runtime` context module | partial | Use `get_run_context()` → `RunContext` (flow/task ids, names, parameters, deployment fields when claimed). Not a full `prefect.runtime` module clone |
| Prefect pause / cancel | partial | Cancel = terminate. Operator pause requires explicit `mode=drain` or `mode=terminate`. Hard OS-kill needs **`ProcessPoolTaskRunner`**; thread runners are cooperative-only. Guide: [cancel / pause / resume](cancel-pause-resume.md) |
| Artifacts (`create_markdown`, …) | unsupported | Internal result artifacts + GET only; no Prefect `create_*` API |
| Automations / webhooks | unsupported | Events + SSE exist; no trigger engine |
| `concurrency` / `rate_limit` (sync) | partial | Sync helpers + GCL CRUD (HTTP, `ironflow gcl`, UI Concurrency page) — [concurrency limits](concurrency-limits.md); async helpers not shipped |
| `task.delay` / background tasks | unsupported | Use deployments + workers instead |
| Cloud connect / workspaces / SSO | deliberate | Self-hosted only; no Cloud client |

When behavior diverges, **[Compatibility matrix](../compatibility.md)** is the source of truth for what is intentional versus not yet implemented.
