# DAG visualization and static forecast

IronFlow combines a **static planner** (pre-run graph forecast) with a **run DAG tab** in the local UI so you can inspect wide and long flows without scrolling through hundreds of identical boxes.

## What is the DAG?

The run **DAG** (directed acyclic graph) shows **task dependencies** for a single flow run:

- **Nodes** — tasks or task runs (depending on view mode).
- **Edges** — dependency arrows pointing **downstream** (from prerequisites to dependents).
- **Layout** — auto-selected from graph shape:
  - **Wide fan-out** (many parallel tasks at one step): dependencies flow **left → right**; parallel siblings stack **top → bottom**.
  - **Deep serial chains** (long prerequisite chains): dependencies flow **top → bottom**; rare parallel branches stack **left → right**.

Arrows always follow dependency direction, never execution time left-to-right unless that matches the dependency depth axis.

## View modes

The UI exposes two views. API query parameter names are unchanged (`mode=logical` | `mode=expanded`).

| UI label | API `mode` | What you see |
| --- | --- | --- |
| **Aggregated fan-out** | `logical` | **Planned graph** from the static forecast. **One node per forecast step**; `map()` fan-out is **collapsed** into a single node. Repeated `submit` calls appear as separate planned steps (`task-0`, `task-1`, …). |
| **Task runs** | `expanded` | **One node per task execution** — every mapped child, every submit invocation, with run id suffix on the label. |

Use **Aggregated fan-out** to understand structure; use **Task runs** to debug individual executions at scale (with zoom, pan, and search).

## Static forecast (`static-planner/`)

When a `@flow` starts, IronFlow compiles the flow function source into a **manifest** (nodes + edges) and a **forecast** (task count, critical path, parallelism). This powers the **Aggregated fan-out** DAG mode and assigns **`planned_node_id`** values to task runs at execution time.

### What the planner analyzes

- `task.submit(...)` and `task.map(...)` inside the `@flow` body
- `wait_for=[...]` dependency lists
- Constant bounded loops: `for i in range(3): ...`
- **`@task(name="custom")`** — resolved via `TaskWrapper` objects visible on the flow module or in the flow closure
- **Repeated calls** to the same task — each call becomes a separate node labeled `task-0`, `task-1`, …
- **Distinct wrappers** on a shared function — e.g. `task(name="ping-start")(fn)` and `task(name="ping-end")(fn)` are separate nodes

### Fallback to runtime

When analysis cannot see the shape (dynamic `range(n)`, `if` branches, tasks not visible to the compiler), the manifest is marked **`fallback_required`** and the UI builds a **runtime-inferred** DAG from recorded task runs. Labels still use `task-N` instance suffixes where possible.

## Run detail → DAG tab

Open any flow run → **DAG** tab.

| Control | Behavior |
| --- | --- |
| **Aggregated fan-out** | Planned graph; `map()` fan-out collapsed |
| **Task runs** | One node per task execution |
| **Fit / Reset** | Fit graph to viewport or reset zoom |
| **Search** | Match task id, label, or name; zoom to selection; **Enter** cycles multiple matches |
| **Click node** | Focus and highlight upstream + downstream path |
| **Scroll / drag** | Zoom toward cursor; pan the canvas |

A definition line under the toolbar restates the active view and layout direction (e.g. “Dependencies: left → right · parallel tasks: top → bottom”).

### Seed large graphs for manual testing

With the API on `http://127.0.0.1:8000`:

```bash
curl -X POST http://127.0.0.1:8000/benchmark/run \
  -H 'Content-Type: application/json' \
  -d '{"flavor":"wide","complexity":100}'

curl -X POST http://127.0.0.1:8000/benchmark/run \
  -H 'Content-Type: application/json' \
  -d '{"flavor":"long_chain","complexity":100}'
```

Then open the run in the UI at `http://localhost:4173/runs` and use the DAG tab. See **[Optional: verify the web UI](../ui_e2e_visual_check.md)** for the full checklist.

## API

- `GET /api/flow-runs/{id}/dag?mode=logical|expanded` — DAG nodes, edges, forecast metadata, `source` (`forecast` or `runtime`), and warnings.
  - `logical` = Aggregated fan-out / planned graph
  - `expanded` = per task run

Task runs expose `planned_node_id` when the forecast could assign one; the UI uses this to color Aggregated fan-out nodes during live updates.
