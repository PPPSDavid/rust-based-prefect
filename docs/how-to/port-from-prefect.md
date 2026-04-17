# How to port a flow from Prefect

IronFlow is **subset-compatible** with Prefect 3.x patterns, not a drop-in replacement for the full Prefect OSS or Cloud runtime. Use this checklist when moving an existing flow.

1. **Swap imports** — Replace `from prefect import flow, task` (and related helpers) with **`from prefect_compat import ...`**. See **[Prefect → IronFlow](../PREFECT_IRONFLOW_MAPPING.md)** for a full concept map.
2. **Wire the control plane** — Register an **`InMemoryControlPlane`** (or the persistence-backed plane your app uses) with **`set_control_plane`** before running flows, matching patterns in **`python-shim/tests/`** and **[Quick start (demo flow)](../QUICKSTART_DEMO.md)**.
3. **Stay inside the supported subset** — Prefer **`submit`** chains, **`map`** with clear shape, retries/timeouts/cancellation as documented. Avoid relying on blocks, full deployment/work-pool parity, or Prefect Cloud–only features until **[Compatibility matrix](../compatibility.md)** says otherwise.
4. **Rename hooks if you use them** — Prefect’s named lifecycle hooks are not mirrored literally. Use IronFlow **`transition_hooks`** with **`TransitionHookSpec`** / **`on_transition`**; see **[Flows](../concepts/flows.md)** and the compatibility matrix.
5. **Validate** — Run **`python -m pytest python-shim/tests`** and your own scripts locally; add a small smoke script under `scripts/` if you want a repeatable check.

When behavior diverges, **[Compatibility matrix](../compatibility.md)** is the source of truth for what is intentional versus not yet implemented.
