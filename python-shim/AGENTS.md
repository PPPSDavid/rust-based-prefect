# AGENTS — python-shim

Ownership: Prefect-compatible authoring/runtime (`prefect_compat`), HTTP server, Python↔Rust bridge.

Read first: root `AGENTS.md`, `COMPATIBILITY.md`, `docs/compatibility_review_workflow.md`.

## In scope

- `@flow` / `@task`, submit/map, deployments, schedules (Python fallbacks)
- FastAPI routes (`server.py` + `routes/`) and persistence glue
- Thin FFI wrappers around rust-engine

## Hotspots (single-writer)

- `src/prefect_compat/__init__.py` (public exports)
- `src/prefect_compat/server.py` (app + embedded worker/scheduler)
- `src/prefect_compat/runtime.py` (control-plane facade)
- `pyproject.toml`

## Validate

```bash
python3 -m pytest python-shim/tests
python3 scripts/code_metrics.py
```

New `python-shim/src` files must be ≤800 lines (see root `CONTRIBUTING.md` / `scripts/code_metrics.py`). Do not grow allowlisted god files.

Compatibility claim changes must update `COMPATIBILITY.md` in the same change.
