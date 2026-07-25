# How to resume tasks and persist results

On **flow-run retry** (or an in-process resume), IronFlow can skip DAG nodes that already **COMPLETED** in a prior attempt. This is **resume-within-lineage**, not Prefect’s full `cache_policy` matrix.

Normative limits: **[Compatibility matrix](../compatibility.md)**. Design notes: `docs/plans/task-result-cache.md` (in-repo).

## When tasks skip

| Prior result | Skip on resume? |
| --- | --- |
| Returned `None` | **Yes** (completion marker; automatic) when params + inputs match |
| Non-`None` + `@task(persist_result=True)` and JSON-safe | **Yes** (value restored) when params + inputs match |
| Non-`None` without `persist_result` | **No** — recomputed |
| Flow/deployment parameters changed vs prior attempt | **No** — all resume skips disabled for that run |
| Submit/`map` inputs changed (or not JSON-fingerprintable) | **No** — that node recomputes |
| Fresh run / new schedule tick (no resume lineage) | **Never** |

Identity is the logical DAG slot:

`(resume_lineage_id, planned_node_id, map_index, input_fingerprint)`

— not bare `task_name` and not a global Python-function cache. `map` children share a `planned_node_id` and distinguish fan-out via `map_index`.

## Authoring

```python
from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task

set_control_plane(InMemoryControlPlane())

@task  # None → auto resume-skip (same params + inputs)
def setup() -> None:
    ...

@task(persist_result=True)  # JSON-safe values restore on resume
def expensive(x: int) -> dict:
    return {"x": x, "n": 42}

@task  # non-None, no persist → recomputed on resume
def volatile(x: int) -> int:
    return x + 1

@flow
def pipeline(x: int = 1) -> int:
    setup.submit()
    payload = expensive.submit(x)
    return volatile.submit(payload.result()["n"]).result()
```

### JSON-safe allowlist (v1)

Persisted payloads **and** input fingerprints must be encodable as JSON:

- Allowed: `None`, `bool`, `int`, `float` (finite), `str`, nested `list` / `dict` with string keys
- Rejected for now: `bytes`, `datetime` / `UUID`, tuples/sets, Path/files, pandas/numpy, arbitrary objects / pickle
- Size cap: **64 KiB** encoded (oversize → live run still succeeds; resume cannot skip)

If encode fails, the task still **COMPLETED**; resume simply recomputes.

### Hooks on resume

Resume cache hits still emit control-plane `PENDING` / `RUNNING` / `COMPLETED` events (with `cache_hit=true`) for UI/API observability, but **do not** re-invoke `@task(transition_hooks=...)` callbacks.

## Triggering resume

**Deployment-backed retry (UI / API):**

`POST /api/flow-runs/{id}/retry` creates a new deployment run with `resume_from_flow_run_id` set to the cancelled/failed run. Eligible completed tasks skip when the worker re-executes the flow with the **same** resolved parameters.

**In-process (tests / scripts):**

```python
plane.prepare_resume(prior_flow_run_id)
pipeline(x=1)  # next create_flow_run consumes the pending resume
```

Calling `pipeline(x=2)` after a prior `pipeline(x=1)` disables resume skips for that run (parameter guard).

## UI

On a completed run’s detail page:

- **Task Runs** — shows pretty-printed JSON under tasks that persisted a result (`null` for `None`); cache hits may show `· resumed`
- **Artifacts** — `*-result` rows with the same payload when `persisted`

Visual seed + Playwright check: `POST /benchmark/run` with
`{"flavor":"persist_result","complexity":7}` (used by
`frontend/e2e/persist-result-ui.spec.ts`), or offline
`scripts/seed_persist_result_ui.py` for manual UI checks.

## Not Prefect cache parity

- No `cache_policy` / `cache_key_fn` / cross-flow sharing by default
- Gates are never resume-skipped; subflow resume policies are still expanding
- Do not assume every completed task skips on retry unless it returned `None` or used `persist_result` **and** params/inputs still match

See also: **[Tasks](../concepts/tasks.md)**, **[How to port a flow from Prefect](port-from-prefect.md)**, Prefect’s upstream [Caching](https://docs.prefect.io/v3/concepts/caching) (different model).
