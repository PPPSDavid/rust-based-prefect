# UI Prefect Parity Checklist

Side-by-side comparison notes for IronFlow UI vs Prefect OSS 3.x.

**Last audited:** 2026-07-25 against `frontend/src/App.tsx` routes and page implementations.

## Navigation

| Area | Prefect | IronFlow today |
| --- | --- | --- |
| Primary nav | Flow Runs, Flows, Deployments, Work Pools | Same top-level sections (`AppShell`: `/runs`, `/flows`, `/deployments`, `/work-pools`) |
| Run detail | Actions (cancel, retry), tabs for tasks/logs | Cancel/retry/pause drain|terminate/resume + tabs (tasks, logs, events, artifacts, DAG) |

## Run detail — artifacts / results

- [x] Task Runs tab shows persisted JSON / `null` when artifact summary includes `result`
- [x] Artifacts tab pretty-prints persisted payloads (metadata-only summaries stay inline)
- [ ] Dedicated Prefect-style result explorer / download for large blobs (out of Phase 1)

## Flow Runs

- [x] List with state filters and pagination (`RunsPage`: state chips + cursor “Load more”)
- [x] Run detail with live SSE updates (`useSsePulse` + `/api/stream/flow-runs/{id}`)
- [x] Cancel active runs (`POST /api/flow-runs/{id}/cancel` when `SCHEDULED` / `PENDING` / `RUNNING`)
- [x] Pause with explicit drain vs terminate chooser (`POST …/pause`); Resume for operator pauses only
- [x] Lifecycle badges (operator pause vs gate wait, drain pending)
- [x] Logs tab search + task/level filters
- [x] Retry failed deployment-backed runs (`POST …/retry`; non-deployment runs surface an error)
- [x] Run detail tabs: Task Runs, Logs, Events, Artifacts, DAG (logical / expanded modes)
- [ ] Concurrency-limits admin surface (API exists; no UI page — backlog)

## Flows

- [x] Flow catalog with run counts (`FlowsPage`)
- [x] Flow detail with tasks and linked deployments (`FlowDetailPage`)

## Deployments

- [x] Dedicated deployments list (not buried under flows) (`/deployments`)
- [x] Quick run with parameter JSON editor (`QuickRunModal`)
- [x] Pause/resume deployment (detail page patch `{ paused }`)
- [x] Deployment run history on detail page

## Work Pools (MVP)

- [x] Process-type work pools list (`WorkPoolsPage`; create process pools)
- [x] Worker status from heartbeats (`WorkPoolDetailPage` polls workers; runtime marks `ONLINE` / `OFFLINE`)
- [x] Pool pause/resume
- [x] Deployment shows assigned work pool id (detail field; defaults to `default-process-pool` label when unset — not a full assign/reassign UI)

## Explicit non-goals (MVP)

- Push/managed pool types
- Work queue priority/concurrency
- Prefect Cloud auth/tenancy
- Pixel-perfect visual clone of Prefect UI
- UI concurrency admin (tracked separately; see `COMPATIBILITY.md`)

## Visual audit procedure

1. Start IronFlow API + UI (`scripts/ironflow_server.py`, `npm run dev` in `frontend/`)
2. Seed data: `python scripts/ui_e2e_seed.py`
3. For persisted task results: Playwright seeds via `POST /benchmark/run` `flavor=persist_result`, or manually `PYTHONPATH=python-shim/src python scripts/seed_persist_result_ui.py` then point `IRONFLOW_HISTORY_PATH` at that history
4. Optionally start Prefect OSS for reference: `prefect server start`
5. Capture screenshots per section and note functional deltas above
