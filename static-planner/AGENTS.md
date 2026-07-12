# AGENTS — static-planner

Ownership: static graph IR, forecast, analyzable `@flow` body subset.

Read first: root `AGENTS.md`, `COMPATIBILITY.md` (Phase 2), `docs/concepts/dag-and-forecast.md`.

## In scope

- AST extraction for submit/map/wait_for / bounded loops
- Manifest + forecast fields consumed by API/UI

## Validate

```bash
python3 -m pytest static-planner/tests
```

Keep fallback behavior (`fallback_required`, runtime DAG) explicit when narrowing or expanding the analyzable subset.
