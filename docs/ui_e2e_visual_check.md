# UI End-to-End Visual Check

Use this checklist to quickly verify the full backend-to-UI path.

## Preconditions

- API server running on `http://127.0.0.1:8000`
- UI dev server running on `http://localhost:4173` (use `localhost`, not `127.0.0.1`, for the Vite dev server)

Start both with:

```bash
python scripts/ironflow_server.py start
```

## 1) Seed test runs

From repo root:

```bash
python scripts/ui_e2e_seed.py
```

Optional custom seed volume:

```bash
python scripts/ui_e2e_seed.py --mapped 4 --chained 4 --complexity 10
python scripts/ui_e2e_seed.py --mapped 2 --chained 2 --failing 2 --complexity 8
```

**Wide and long DAG stress** (via benchmark API):

```bash
curl -X POST http://127.0.0.1:8000/benchmark/run \
  -H 'Content-Type: application/json' \
  -d '{"flavor":"wide","complexity":100}'

curl -X POST http://127.0.0.1:8000/benchmark/run \
  -H 'Content-Type: application/json' \
  -d '{"flavor":"long_chain","complexity":100}'
```

(`mapped` / `chained` flavors are accepted as aliases for `wide` / `long_chain`.)

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

- **Task Runs**: multiple task rows present; when tasks used **`persist_result`** (or returned `None`), pretty-printed JSON / `null` may appear under the row
- **Logs**: log entries present
- **Events**: state transitions and task events present
- **Artifacts**: result artifacts present for completed task events; persisted payloads show as pretty JSON when `summary` includes `result`
- **DAG**:
  - **Aggregated fan-out** mode renders the forecast/manifest graph (`source: forecast` when static compile succeeded; `map()` fan-out collapsed)
  - **Task runs** mode renders one node per execution
  - Definition line: dependencies **left → right**, parallel tasks **top → bottom**
  - Toolbar: **Fit**, **Reset**, **Aggregated fan-out** / **Task runs** toggle
  - **Search** finds a task by id/label/name, zooms to it, and highlights upstream/downstream path; **Enter** cycles multiple matches
  - **Scroll** zooms; **drag** pans the canvas (smooth GPU transform)
  - Wide runs (`wide_flow`): Aggregated fan-out mode collapses fan-out; Task runs lists individual mapped runs
  - Long chains (`long_chain_flow`): top-to-bottom dependency layout; zoom/pan to navigate
  - Failing runs: downstream nodes may show `NOT_REACHABLE`

See **[DAG and forecast](concepts/dag-and-forecast.md)** for details.

## 3) Persistence check

Stop and restart API server, then refresh UI:

- `http://localhost:4173/runs` should still show previously seeded runs

This confirms persisted data is being picked up from local storage.

## README screenshots

Landing-page shots live in **`docs/assets/readme/`**: `ui-runs.png`, `ui-deployments.png`, `ui-run-dag.png` (still), plus a short DAG clip (`ui-run-dag.mp4` with `ui-run-dag.gif` fallback). Recapture them after major UI changes (same seed flow as above, ~1280px wide). The DAG clip should show **Aggregated fan-out** → **Task runs** → **Fit**.
