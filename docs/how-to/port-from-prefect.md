# How to port a flow from Prefect

IronFlow is **subset-compatible** with Prefect 3.x patterns, not a drop-in replacement for the full Prefect OSS or Cloud runtime. Use this checklist when moving an existing flow.

1. **Swap imports** — Replace `from prefect import flow, task` (and related helpers) with **`from prefect_compat import ...`**. See **[Prefect → IronFlow](../PREFECT_IRONFLOW_MAPPING.md)** for a full concept map.
2. **Wire the control plane** — Register an **`InMemoryControlPlane`** (or the persistence-backed plane your app uses) with **`set_control_plane`** before running flows, matching patterns in **`python-shim/tests/`** and **[Quick start (demo flow)](../QUICKSTART_DEMO.md)**.
3. **Stay inside the supported subset** — Prefer **`submit`** chains, **`map`** with clear shape, retries/timeouts/cancellation as documented. For nesting, use IronFlow’s **two subflow mechanisms** ([how-to](subflows.md)) instead of Prefect-only APIs. For expensive tasks you want skipped on flow-run **retry**, use **`@task(persist_result=True)`** (JSON-safe values) or return **`None`** (auto marker) — see **[How to resume tasks and persist results](task-resume-and-persist.md)**. Do not expect Prefect `cache_policy` / cross-flow caching. Avoid relying on blocks, full deployment/work-pool parity, or Prefect Cloud–only features until **[Compatibility matrix](../compatibility.md)** says otherwise.
4. **Rename hooks if you use them** — Prefect’s named lifecycle hooks are not mirrored literally. Use IronFlow **`transition_hooks`** with **`TransitionHookSpec`** / **`on_transition`**; see **[Flows](../concepts/flows.md)** and the compatibility matrix.
5. **Map Prefect caching carefully** — Prefect’s default cache key (inputs + task source + flow run) is **not** implemented. IronFlow resume is DAG-slot + lineage based. Guide: **[task resume and persist](task-resume-and-persist.md)**.
6. **Validate** — Run **`python -m pytest python-shim/tests`** and your own scripts locally; add a small smoke script under `scripts/` if you want a repeatable check.

When behavior diverges, **[Compatibility matrix](../compatibility.md)** is the source of truth for what is intentional versus not yet implemented.
