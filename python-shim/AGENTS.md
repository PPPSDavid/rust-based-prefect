# AGENTS — python-shim

Ownership: Prefect-compatible authoring/runtime (`prefect_compat`), HTTP server, Python↔Rust bridge.

Read first: root `AGENTS.md`, `COMPATIBILITY.md`, `docs/compatibility_review_workflow.md`.

## In scope

- `@flow` / `@task`, submit/map, deployments, schedules (Python fallbacks)
- FastAPI routes and persistence glue
- Thin FFI wrappers around rust-engine

## Hotspots (single-writer)

- `src/prefect_compat/__init__.py` (public exports)
- `src/prefect_compat/server.py` (central routes)
- `pyproject.toml`

## Validate

```bash
python3 -m pytest python-shim/tests
```

Compatibility claim changes must update `COMPATIBILITY.md` in the same change.
