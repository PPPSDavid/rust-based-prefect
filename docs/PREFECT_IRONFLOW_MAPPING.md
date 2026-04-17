# Prefect concepts → IronFlow

This project is **not** a drop-in replacement for Prefect Cloud or the full Prefect OSS runtime. It is a **prototype** that lets you author flows with **Prefect-like** decorators and patterns while execution goes through IronFlow’s control plane (Python shim + Rust engine). Use this table to orient yourself if you already know Prefect 3.x.

**Upstream Prefect (reference only):** the official [Prefect 3 get started guide](https://docs.prefect.io/v3/get-started) explains flows, tasks, and the mental model this repo echoes. Source for Prefect OSS lives at [github.com/prefecthq/prefect](https://github.com/prefecthq/prefect). IronFlow reuses *patterns*, not the Prefect runtime.

| Prefect (typical mental model) | In IronFlow |
| --- | --- |
| `from prefect import flow, task` | `from prefect_compat import flow, task` (and `wait`, `set_control_plane`, etc.). Imports come from the **`prefect_compat`** package in this repo, not from `prefect`. |
| Prefect orchestration / API server | Optional HTTP API in `prefect_compat.server` (e.g. `uvicorn python-shim.src.prefect_compat.server:app`). Start with `python scripts/ironflow_server.py start` or run flows **without** any server—orchestration works in-process. |
| Prefect UI | Optional Vite/React app under `frontend/` when you want a local dashboard; not the Prefect Cloud UI. |
| Deployments, work pools, workers | **Not** production-parity. Some deployment-shaped APIs exist for experiments; treat as **subset / prototype**. See `COMPATIBILITY.md`. |
| `task.submit()` / futures | Supported for dependency chains within the MVP subset. |
| `task.map()` | Supported with moderate fan-out (see `COMPATIBILITY.md`). |
| Retries, timeouts, cancellation | Enforced at the **control-plane** level for supported flows; semantics are workload-driven—see `COMPATIBILITY.md` for exact boundaries. |
| Blocks, integrations, secrets | **Not** a focus of the MVP; many patterns are unsupported or stubbed. |
| State hooks (`on_running`, …) | IronFlow uses **`transition_hooks`** on `@flow` / `@task` with `TransitionHookSpec` / `on_transition`—see `COMPATIBILITY.md`. This is an **extension**, not Prefect’s hook API. |
| Event stream / observability | Local persistence (JSONL + SQLite) and optional API/SSE; see README **History persistence**. |
| Static DAG / compile-time insights | `static-planner/` for analyzable subsets; dynamic or opaque regions fall back to runtime behavior (see `docs/architecture.md`). |

## Practical “bring your own tasks” path

1. **Clone** the repo and create the conda env from `environment.yml` (or install `requirements-ci.txt` in a venv).
2. **Port imports**: replace `prefect` flow/task imports with `prefect_compat` (and wire `set_control_plane` / `InMemoryControlPlane` as in tests under `python-shim/tests/`).
3. **Stay inside the subset**: prefer `submit` chains, `map` with clear static shape, and control-plane features listed in `COMPATIBILITY.md`.
4. **Validate**: run `python -m pytest python-shim/tests` and your own scripts locally; add a small script under `scripts/` if you want a repeatable smoke test.
5. **Optional UI/API**: start `scripts/ironflow_server.py` to inspect runs that were persisted to disk.

When something behaves differently from Prefect, **`COMPATIBILITY.md`** is the source of truth for what is intentional versus not yet implemented.
