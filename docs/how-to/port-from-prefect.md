# How to port a flow from Prefect

IronFlow is **subset-compatible** with Prefect 3.x patterns, not a drop-in replacement for the full Prefect OSS or Cloud runtime. Use this checklist when moving an existing flow.

1. **Swap imports** — Replace `from prefect import flow, task` (and related helpers) with **`from prefect_compat import ...`**. See **[Prefect → IronFlow](../PREFECT_IRONFLOW_MAPPING.md)** for a full concept map.
2. **Wire the control plane** — Register an **`InMemoryControlPlane`** (or the persistence-backed plane your app uses) with **`set_control_plane`** before running flows, matching patterns in **`python-shim/tests/`** and **[Quick start (demo flow)](../QUICKSTART_DEMO.md)**.
3. **Stay inside the supported subset** — Prefer **`submit`** chains, **`map`** with clear shape, retries/timeouts/cancellation as documented. For nesting, use IronFlow’s **two subflow mechanisms** ([how-to](subflows.md)) instead of Prefect-only APIs. Avoid relying on blocks, full deployment/work-pool parity, or Prefect Cloud–only features until **[Compatibility matrix](../compatibility.md)** says otherwise.
4. **Rename hooks if you use them** — Prefect’s named lifecycle hooks are not mirrored literally. Use IronFlow **`transition_hooks`** with **`TransitionHookSpec`** / **`on_transition`**; see **[Flows](../concepts/flows.md)** and the compatibility matrix.
5. **Validate** — Run **`python -m pytest python-shim/tests`** and your own scripts locally; add a small smoke script under `scripts/` if you want a repeatable check.

## Common Prefect APIs → IronFlow status

Use this table when a Prefect import fails or behaves differently. Status values: **supported** (use the IronFlow equivalent), **partial** (subset / rename), **unsupported** (rewrite or wait), **deliberate** (intentional divergence — do not expect Prefect parity).

| Prefect import / API | IronFlow status | What to do |
| --- | --- | --- |
| `from prefect import flow, task` | supported | `from prefect_compat import flow, task` |
| `task.submit` / `wait_for` / `wait` | supported | Same patterns; default flow finalization is **`wait_all`** (see matrix) |
| `task.map` | supported | Moderate fan-out; pick a runner via [how-to](choose-task-runners.md) |
| `flow` / `task` retries & timeouts | supported | Control-plane enforced for the documented subset |
| `get_run_logger` / `log_prints=` | unsupported | No helper yet; use stdlib logging. Logs still appear in UI/API when written to the store by the runtime path |
| `on_running` / `on_failure` / … hooks | deliberate | Map to `transition_hooks=` + `on_transition(...)` edges |
| `run_deployment` / nested-flow helpers | partial | Use `deployment_ref(...).submit()` or inline `child_flow(...)` — [subflows](subflows.md) |
| Blocks (`prefect.blocks.*`) | deliberate | Not in scope; use env vars / your own config |
| `cache_policy` / task result cache | unsupported | Retry re-runs completed tasks today; resume/cache tracked in PR #50 |
| Variables (`prefect.variables`) | unsupported | Pass parameters or read env / files |
| Secrets / profiles / settings | unsupported | Env vars + optional [Basic auth](secure-self-hosted.md) |
| `prefect.runtime` context module | unsupported | No stable `runtime` module; flow/task ids live on control-plane records |
| Artifacts (`create_markdown`, …) | unsupported | Internal result artifacts + GET only; no Prefect `create_*` API |
| Automations / webhooks | unsupported | Events + SSE exist; no trigger engine |
| `concurrency` / `rate_limit` (sync) | partial | Sync helpers + GCL CRUD — [concurrency limits](concurrency-limits.md); async helpers not shipped |
| `task.delay` / background tasks | unsupported | Use deployments + workers instead |
| Cloud connect / workspaces / SSO | deliberate | Self-hosted only; no Cloud client |

When behavior diverges, **[Compatibility matrix](../compatibility.md)** is the source of truth for what is intentional versus not yet implemented.
