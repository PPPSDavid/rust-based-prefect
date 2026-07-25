# UI Prefect Parity Checklist

Side-by-side comparison notes for IronFlow UI vs Prefect OSS 3.x.

**Last audited:** 2026-07-25 against `frontend/src/App.tsx` routes and page implementations.

## Navigation

| Area | Prefect | IronFlow today |
| --- | --- | --- |
| Primary nav | Flow Runs, Flows, Deployments, Work Pools | Same top-level sections (`AppShell`: `/runs`, `/flows`, `/deployments`, `/work-pools`) |
| Run detail | Actions (cancel, retry), tabs for tasks/logs | Cancel/retry action bar + tabs (tasks, logs, events, artifacts, DAG) |

## Flow Runs

- [x] List with state filters and pagination (`RunsPage`: state chips + cursor “Load more”)
- [x] Run detail with live SSE updates (`useSsePulse` + `/api/stream/flow-runs/{id}`)
- [x] Cancel active runs (`POST /api/flow-runs/{id}/cancel` when `SCHEDULED` / `PENDING` / `RUNNING`)
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
- [x] Worker ONLINE/OFFLINE status (`WorkPoolDetailPage` + heartbeat polling)
- [x] Pool pause/resume
- [x] Deployment assigned to work pool (shown on deployment detail; default pool name when unset)

## Explicit non-goals (MVP)

- Push/managed pool types
- Work queue priority/concurrency
- Prefect Cloud auth/tenancy
- Pixel-perfect visual clone of Prefect UI
- UI concurrency admin (tracked separately; see `COMPATIBILITY.md`)

## Visual audit procedure

1. Start IronFlow API + UI (`scripts/ironflow_server.py`, `npm run dev` in `frontend/`)
2. Seed data: `python scripts/ui_e2e_seed.py`
3. Optionally start Prefect OSS for reference: `prefect server start`
4. Capture screenshots per section and note functional deltas above
