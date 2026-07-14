# UI Prefect Parity Checklist

Side-by-side comparison notes for IronFlow UI vs Prefect OSS 3.x.

## Navigation

| Area | Prefect | IronFlow (target) |
| --- | --- | --- |
| Primary nav | Flow Runs, Flows, Deployments, Work Pools | Same top-level sections |
| Run detail | Actions (cancel, retry), tabs for tasks/logs | Cancel/retry action bar + tabs |

## Run detail — artifacts / results

- [x] Task Runs tab shows persisted JSON / `null` when artifact summary includes `result`
- [x] Artifacts tab pretty-prints persisted payloads (metadata-only summaries stay inline)
- [ ] Dedicated Prefect-style result explorer / download for large blobs (out of Phase 1)

## Flow Runs

- [ ] List with state filters and pagination
- [ ] Run detail with live SSE updates
- [ ] Cancel active runs
- [ ] Retry failed deployment-backed runs

## Flows

- [ ] Flow catalog with run counts
- [ ] Flow detail with tasks and linked deployments

## Deployments

- [ ] Dedicated deployments list (not buried under flows)
- [ ] Quick run with parameter JSON editor
- [ ] Pause/resume deployment
- [ ] Deployment run history on detail page

## Work Pools (MVP)

- [ ] Process-type work pools list
- [ ] Worker ONLINE/OFFLINE status
- [ ] Pool pause/resume
- [ ] Deployment assigned to work pool

## Explicit non-goals (MVP)

- Push/managed pool types
- Work queue priority/concurrency
- Prefect Cloud auth/tenancy
- Pixel-perfect visual clone of Prefect UI

## Visual audit procedure

1. Start IronFlow API + UI (`scripts/ironflow_server.py`, `npm run dev` in `frontend/`)
2. Seed data: `python scripts/ui_e2e_seed.py`
3. For persisted task results: `PYTHONPATH=python-shim/src python scripts/seed_persist_result_ui.py` then point `IRONFLOW_HISTORY_PATH` at that history (or run `frontend/e2e/persist-result-ui.spec.ts`)
4. Optionally start Prefect OSS for reference: `prefect server start`
5. Capture screenshots per section and note functional deltas above
