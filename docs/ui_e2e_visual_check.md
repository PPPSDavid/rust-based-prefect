# UI End-to-End Visual Check

Use this checklist to quickly verify the full backend-to-UI path.

## Preconditions

- API server running on `http://127.0.0.1:8000`
- UI dev server running on `http://localhost:4173`

## 1) Seed test runs

From repo root:

- `python scripts/ui_e2e_seed.py`

Optional custom seed volume:

- `python scripts/ui_e2e_seed.py --mapped 4 --chained 4 --complexity 10`
- Include failure-path DAG coverage:
  - `python scripts/ui_e2e_seed.py --mapped 2 --chained 2 --failing 2 --complexity 8`

## 2) Visual checks in UI

Open `http://localhost:4173/runs` and verify:

- Top navigation shows **Flow Runs**, **Flows**, **Deployments**, and **Work Pools**
- Runs table shows recent runs with state filter chips
- State badges are visible (`COMPLETED` expected for benchmark runs)
- Clicking a run opens run detail page with Cancel/Retry actions when applicable

### Deployments

- `/deployments` lists deployments with Quick Run
- Deployment detail shows schedule, parameters, and recent deployment runs

### Work Pools

- `/work-pools` shows `default-process-pool`
- Pool detail lists workers (local worker should appear as `ONLINE` when server worker is enabled)

Inside run detail verify tabs:

- **Task Runs**: multiple task rows present
- **Logs**: log entries present
- **Events**: state transitions and task events present
- **Artifacts**: result artifacts present for completed task events
- **DAG**:
  - logical mode renders task graph
  - expanded mode renders task-run graph
  - node colors track task states (`RUNNING`, `COMPLETED`, `FAILED`, etc.)
  - for failing runs, downstream node may appear as `NOT_REACHABLE`

## 3) Persistence check

Stop and restart API server, then refresh UI:

- `http://localhost:4173/runs` should still show previously seeded runs

This confirms persisted data is being picked up from local storage.
