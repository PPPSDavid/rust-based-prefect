# Subflows Design Plan (IronFlow)

**Status:** Implemented (Phases 0–5 landed on `main` via PRs #34 and #36)  
**Last updated:** 2026-07-12  
**Scope:** `rust-engine/`, `python-shim/`, `static-planner/`, `frontend/`, `benchmarks/`  
**User-facing docs:** [How to compose flows with subflows](../how-to/subflows.md) · [Flows](../concepts/flows.md) · [Compatibility matrix](../../COMPATIBILITY.md)

This document is the **historical design plan** for IronFlow subflows. Prefer the user guide and compatibility matrix for “how do I use this?”. Keep this file for design rationale and phase history. (`docs/plans/**` is **not** published to the MkDocs site.)

The goal was a **simpler, two-mechanism model** than Prefect’s full subflow matrix, with **deployment-backed execution**, **arbitrary nesting**, and **performance-first** benchmarks per phase.

---

## 1. Problem statement (original)

Before Phases 0–4, IronFlow had **no first-class subflow support**. Calling one `@flow` from another could create an **unlinked sibling flow run** with no parent relationship, no `.submit()` API, and no deployment routing. That was insufficient for real orchestration where:

- Child work may need a **different worker / work pool** than the parent.
- Parents need **downstream task dependencies** on subflow completion (or fire-and-forget).
- Users need **nested** composition (subflows calling subflows).
- The UI must remain understandable without Prefect-level complexity.

**Current status:** M1 (inline) and M2 (`deployment_ref.submit`) ship on `main` with linkage, cancel propagation, DAG/UI navigation, and `perf_matrix` presets `subflow_lite` / `subflow`.

---

## 2. Design principles

1. **Two mechanisms only** — blocking inline vs deployment-backed task. No third “magic” mode.
2. **Deployment is the routing unit for async subflows** — a subflow-as-task always resolves to a **deployment** so work pool, entrypoint, and parameters are explicit.
3. **Nesting is recursive** — either mechanism can appear inside either mechanism, at any depth, with a consistent parent chain in the data model.
4. **Rust owns hot paths** — enqueue, claim, wait, state mirroring, and linkage writes prefer `rust-engine` + SQLite; Python remains the authoring surface.
5. **Benchmark every phase** — each deliverable adds `perf_matrix` recipes and threshold gates; no feature merges without measured baselines.
6. **UI follows semantics** — inline subflows expand in-place; task-mode subflows are a single navigable node.

---

## 3. Two mechanisms (authoritative)

### Mechanism 1 — Blocking inline (`child(...)`)

| Aspect | Behavior |
| --- | --- |
| **API** | Direct call: `result = child_flow(arg, kw=...)` |
| **Execution** | Same process, same worker, same call stack; parent blocks until child body returns |
| **Routing** | **No deployment enqueue** — inherits parent’s execution context |
| **Downstream deps** | Via plain Python values: `downstream.submit(child(x))` |
| **Fire-and-forget** | N/A — always synchronous |
| **Control plane** | Child tasks recorded under a **linked inline child flow run** (see §5) OR rolled into parent flow run (implementation choice in §5.1) |
| **UI** | Collapsed **inline subflow** node in parent DAG; expand shows mini start → tasks → end diagram |

**Nested use:** A blocking child may internally call `grandchild.submit(...)` (mechanism 2) or `grandchild(...)` (mechanism 1). The parent only blocks on the direct inline call.

### Mechanism 2 — Subflow as task (`child.submit(...)` / deployment reference)

| Aspect | Behavior |
| --- | --- |
| **API** | `future = child.submit(..., wait_for=[...])` where `child` is bound to a **deployment** |
| **Execution** | Parent enqueues `deployment_run` for the child deployment; **child worker** (per deployment work pool) executes the flow |
| **Routing** | **`trigger_deployment_run(child_deployment_id, parameters)`** — work pool on the deployment determines the worker |
| **Downstream deps** | `wait_for=[subflow_future]` on subsequent `task.submit` / `subflow.submit` |
| **Fire-and-forget** | `child.submit(...)` without awaiting `.result()` |
| **Control plane** | Surrogate **parent task run** (`kind: subflow`) + child **flow run** + child **deployment run**, all linked (see §5) |
| **UI** | Single task-shaped node in parent logical DAG; **click navigates** to child flow run page (no in-place nesting) |

**Nested use:** Child flow running on its worker may call another deployment-backed subflow or a blocking inline subflow. Parent chain fields (§5) must support arbitrary depth.

---

## 4. Public API (target)

### 4.1 Binding flows to deployments

Subflow-as-task requires a deployment reference. Proposed surface:

```python
from prefect_compat import flow, task, wait
from prefect_compat.deployments import deployment_ref  # new

@flow
def child_flow(n: int) -> int:
    ...

# Registered via ironflow deploy / API — returns a handle
child_deploy = deployment_ref("child-flow/my-deployment")

@flow
def parent_flow(x: int) -> int:
    # Mechanism 1 — blocking inline (in-process, same worker)
    a = child_flow(x)

    # Mechanism 2 — deployment-backed subflow as task
    f = child_deploy.submit(n=a, wait_for=[])
    b = other_task.submit(f, wait_for=[f]).result()

    # Fire-and-forget
    child_deploy.submit(n=99)

    return b
```

**Resolution rules:**

- `@flow` without deployment binding: **only mechanism 1** (`child_flow(...)`) is allowed; `.submit()` raises a clear error directing users to create a deployment.
- `deployment_ref(name)` or `deployment_ref(deployment_id)`: enables **mechanism 2** via `.submit()`.
- Parameters: merge deployment `default_parameters` with call-time kwargs (same rules as `trigger_deployment_run`).

### 4.2 Futures

`SubflowFuture` extends the existing `TaskFuture` shape:

```python
@dataclass
class SubflowFuture(Generic[T]):
    # Populated progressively as child run starts
    deployment_run_id: str | None
    child_flow_run_id: str | None
    parent_task_run_id: str          # surrogate task in parent
    planned_node_id: str | None

    def result(self) -> T: ...       # blocks until child terminal; raises on FAILED/CANCELLED
```

`wait_for` accepts `TaskFuture` and `SubflowFuture` interchangeably.

### 4.3 Nested subflows (examples)

```python
@flow
def leaf(n: int) -> int: ...

leaf_deploy = deployment_ref("leaf/prod")

@flow
def mid(n: int) -> int:
    inline = leaf(n)                    # M1 inside worker executing mid
    async_ = leaf_deploy.submit(n=inline) # M2 to possibly different pool
    return async_.result()

mid_deploy = deployment_ref("mid/prod")

@flow
def root() -> int:
    return mid_deploy.submit(n=1).result()  # M2 → M2/M1 chain
```

---

## 5. Control-plane data model

### 5.1 Flow runs (`flow_runs`)

Add columns (Rust schema + SQLite migration + Python projection):

| Column | Purpose |
| --- | --- |
| `parent_flow_run_id` | UUID nullable — immediate parent flow run |
| `parent_task_run_id` | UUID nullable — surrogate task that launched this run (M2 only) |
| `execution_mode` | `inline` \| `deployment` |
| `root_flow_run_id` | UUID — top-level ancestor for fast UI breadcrumbs |
| `depth` | int — nesting depth (0 = root) |

**Mechanism 1 recommendation:** Create a **linked child flow run** with `execution_mode=inline` for observability and inline DAG embedding, while execution stays in-process. Child task runs attach to this inline child run (not the parent). Parent remains `RUNNING` until inline child returns.

### 5.2 Deployment runs (`deployment_runs`)

Add columns:

| Column | Purpose |
| --- | --- |
| `parent_flow_run_id` | Who triggered this run |
| `parent_task_run_id` | Surrogate subflow task in parent |
| `parent_deployment_run_id` | Optional — parent’s deployment run when triggered from a worker |

Enables audit trail and cancellation propagation across pools.

### 5.3 Task runs (`task_runs`)

Add:

| Column | Purpose |
| --- | --- |
| `kind` | `task` (default) \| `subflow` |
| `child_flow_run_id` | Set when `kind=subflow` and child flow run is created |
| `child_deployment_run_id` | Set when enqueued |

Surrogate subflow task state mirrors child flow run terminal state:

`SCHEDULED → PENDING → RUNNING → COMPLETED | FAILED | CANCELLED`

### 5.4 Context variables (Python worker)

| Var | Rule |
| --- | --- |
| `_ACTIVE_FLOW_RUN` | Set per executing flow; restored on return |
| `_ACTIVE_DEPLOYMENT_RUN` | **Cleared** during parent wait for child deployment run; child worker sets its own |

Prevents accidental attachment of child flow runs to parent deployment runs.

---

## 6. Execution flows

### 6.1 Mechanism 1 (blocking inline)

```
Parent worker, parent flow RUNNING
  → child_flow(...) entered
  → create inline child flow_run (parent_flow_run_id=parent, execution_mode=inline)
  → set _ACTIVE_FLOW_RUN = child
  → execute child body (tasks recorded on child flow_run)
  → child flow_run → COMPLETED|FAILED
  → restore _ACTIVE_FLOW_RUN = parent
  → return result to parent
```

### 6.2 Mechanism 2 (deployment-backed)

```
Parent worker, parent flow RUNNING
  → child_deploy.submit(params)
  → create surrogate parent task_run (kind=subflow, PENDING)
  → trigger_deployment_run(child_deployment_id, params, parent linkage)
  → return SubflowFuture (deployment_run_id set)
  → [optional] future.result():
       wait_for_deployment_run_terminal(deployment_run_id)  # Rust-backed poll/wait
       mirror surrogate task state from child flow_run
       return child output (from flow result artifact or terminal metadata)
```

Child worker claims deployment run independently, creates its own flow run with parent linkage, executes child flow (which may nest M1/M2 again).

### 6.3 Cancellation

| Scenario | Behavior |
| --- | --- |
| Cancel parent while **inline** child running | Inline stack unwinds; inline child flow_run → CANCELLED; parent → CANCELLED |
| Cancel parent while **M2** child queued/running | Cancel surrogate task; cancel child deployment_run; cancel child flow_run if started |
| Cancel child only | Child terminal; surrogate task → CANCELLED/FAILED; parent continues unless it awaited the future |

---

## 7. UI design

### 7.1 Mechanism 1 — inline subflow node

- **Logical DAG:** node `kind: inline_subflow` spanning a visual group (collapsed by default).
- **Expanded / inline view:** mini-DAG fetched from child flow run manifest + live task states.
- **Breadcrumb:** `root → … → parent → [inline: child_name]` without leaving parent page when collapsed.
- **Nested M2 inside M1:** inside expanded inline view, subflow tasks appear as ordinary task nodes with a **link icon** to child flow run (M2 only).

### 7.2 Mechanism 2 — subflow task node

- **Logical DAG:** one node, same shape as tasks, label = deployment/flow name, state = surrogate task state.
- **Interaction:** click → navigate to `/runs/{child_flow_run_id}`.
- **No in-place nesting** of child DAG in parent view.
- **Run detail header:** show parent link when `parent_flow_run_id` present.

### 7.3 API extensions

| Endpoint | Change |
| --- | --- |
| `GET /api/flow-runs/{id}/dag` | Node `kind`, `child_flow_run_id`, `execution_mode`, `inline_manifest_ref` |
| `GET /api/flow-runs/{id}` | `parent_flow_run_id`, `root_flow_run_id`, `depth`, `children` summary |
| `GET /api/flow-runs/{id}/subflows` | Paginated child flow runs (optional convenience) |

---

## 8. Static planner

| Mechanism | Planner support |
| --- | --- |
| M2 `deployment_ref(...).submit()` | **Analyzable** — treat as task node with deployment name; edges from `wait_for` |
| M1 `child_flow(...)` | **Opaque region** unless compile-time flow reference is statically known (phase 2+) |

Forecast: subflow task nodes contribute to task count and critical path estimates using deployment manifest when available.

---

## 9. Rust vs Python responsibilities

| Operation | Owner | Notes |
| --- | --- | --- |
| `trigger_deployment_run` with parent linkage | **Rust** (`deployment_ops.rs`) | Extend existing op |
| `wait_for_deployment_run_terminal` | **Rust** | New op; blocking wait with backoff (mirror `claim_next_deployment_run_wait` pattern) |
| Surrogate task state mirroring | **Rust** | On child terminal transition, update parent task row in same TX |
| `create_flow_run` with parent fields | **Rust** | Extend `create_flow_run_persist` |
| Cancel propagation (parent → children) | **Rust** | BFS/depth walk with cycle guard |
| `deployment_ref` resolution | Python | Cache deployment id by name |
| `@flow` / `.submit()` decorators | Python | Thin FFI wrappers |
| DAG assembly for UI | Python (query) → Rust (read hot path) | Prefer Rust `ui_read` for list/detail |

---

## 10. Performance & benchmarking

Per **AGENTS.md** and `docs/perf_methodology.md`, each phase must add benchmarks before merge.

### 10.1 New `perf_matrix` recipes

| Recipe | What it measures |
| --- | --- |
| `subflow_inline_depth_3` | M1 nested 3 deep, task fan-out 10 — in-process overhead vs flat flow |
| `subflow_deploy_wait_chain` | M2 chain depth 3, same pool — enqueue + wait latency |
| `subflow_deploy_cross_pool` | M2 parent/child different work pools — trigger + claim + wait |
| `subflow_fire_forget_burst` | 50× M2 submit without wait — control-plane throughput |
| `subflow_cancel_propagation` | Cancel parent with 5 running children — cancel latency |
| `subflow_query_dag_nested` | UI DAG fetch with depth-3 tree — read path latency |

### 10.2 Threshold policy

- Compare against **pre-subflow baseline** on same `matrix_compare_key`.
- Regressions > methodology thresholds require explicit justification in PR.
- Report p50/p95 for: submit→child RUNNING, child terminal→parent task COMPLETED, DAG query.

### 10.3 Fast local gate (per PR)

```bash
python benchmarks/perf_matrix.py run --preset lite --recipes subflow_inline_depth_3,subflow_deploy_wait_chain --repetitions 1 --warmups 0 --jobs 2
```

---

## 11. Compatibility & documentation

**Shipped with the feature (check these first):**

- `COMPATIBILITY.md` — “Subflows (subset)” under Phase 1 runtime compatibility.
- `docs/how-to/subflows.md` — primary user guide (MkDocs How-to nav).
- `docs/concepts/flows.md` — overview + link to how-to.
- `docs/concepts/dag-and-forecast.md` — `inline_subflow` / `subflow_task` node kinds.
- `docs/PREFECT_IRONFLOW_MAPPING.md` — Prefect nested-flow row.
- `docs/index.md`, `docs/concepts/index.md`, `docs/how-to/index.md`, `mkdocs.yml` — discoverability.

Maintainer-only (excluded from published site): this plan under `docs/plans/`.

**Intentionally not in scope (v1):**

- Prefect `SubflowTask` / `run_deployment` API name parity (IronFlow uses `deployment_ref` / `SubflowFuture`).
- Subflow parameter schema validation beyond deployment defaults.
- Automatic deployment creation from `@flow` (users must deploy child flows explicitly).

---

## 12. Phased implementation plan

### Phase status summary

| Phase | Focus | Status |
| --- | --- | --- |
| 0 | Schema & linkage | Done (#34) |
| 1 | M2 deploy submit/wait | Done (#34) |
| 2 | M1 inline | Done (#34) |
| 3 | Cancel + cross-pool | Done (#34) |
| 4 | DAG/UI navigation | Done (#34) |
| 5 | `perf_matrix` recipes + multi-worker Rust fixes | Done (#36) |

### Phase 0 — Schema & linkage foundation

**Deliverables:**
- Rust + SQLite migrations for parent/root/depth fields on `flow_runs`, `deployment_runs`, task `kind` columns.
- FFI ops: extended `create_flow_run`, `trigger_deployment_run` with parent metadata.
- Unit tests: Rust FSM + Python round-trip.

**Benchmark:** schema write overhead (create_flow_run with parent fields vs without).

---

### Phase 1 — Mechanism 2 core (deployment-backed subflow as task)

**Deliverables:**
- `deployment_ref()` handle and `.submit()` on bound flows.
- Surrogate `kind=subflow` task run creation.
- `trigger_deployment_run` from parent with linkage.
- `wait_for_deployment_run_terminal` (Rust) + `SubflowFuture.result()`.
- `wait_for` integration with existing tasks.
- Fix `_ACTIVE_DEPLOYMENT_RUN` context isolation on parent wait.
- Tests: same-pool wait, fire-and-forget, `wait_for` chain, failed child.

**Benchmark:** `subflow_deploy_wait_chain`, `subflow_fire_forget_burst`.

---

### Phase 2 — Mechanism 1 (blocking inline)

**Deliverables:**
- `child_flow(...)` creates linked inline child flow run (stop orphan siblings).
- Task runs attach to inline child run; parent blocks correctly.
- Nested M1+M2 from investigation scenarios.
- Cancellation for inline stack.

**Benchmark:** `subflow_inline_depth_3`.

---

### Phase 3 — Nesting & cancellation hardening

**Deliverables:**
- Arbitrary depth M2→M2→M1 chains.
- Parent cancel propagates to all active children (BFS, depth limit guard).
- `root_flow_run_id` breadcrumbs in API.

**Benchmark:** `subflow_cancel_propagation`, `subflow_deploy_cross_pool`.

---

### Phase 4 — UI

**Deliverables:**
- DAG node kinds: `inline_subflow`, `subflow_task`.
- `RunDagPanel` inline expand for M1.
- Navigation link for M2 nodes.
- Parent/child breadcrumbs on run detail page.

**Benchmark:** `subflow_query_dag_nested` (API-side; optional Playwright smoke).

---

### Phase 5 — Static planner & docs polish

**Deliverables:**
- Planner recognition of `deployment_ref().submit()`.
- Docs listed in §11.
- `COMPATIBILITY.md` matrix row.

---

## 13. Test strategy

| Layer | Coverage |
| --- | --- |
| Rust unit | Parent linkage TX atomicity, wait terminal, cancel propagation |
| Python unit | API semantics, context vars, future `wait_for` |
| Integration | Two-worker cross-pool subflow (script under `scripts/`) |
| E2E | `test_e2e_flow_scripts.py` with subflow chain |
| UI | `RunDetailPage.test.tsx` for new node kinds |

---

## 14. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| Parent wait blocks worker slot | Document; future: optional async parent flows |
| Cross-pool latency | Rust wait op; surrogate task shows RUNNING while child queued |
| Deep nesting UI clutter | Depth cap in breadcrumb; M2 always navigates away |
| Circular subflow references | Static check where possible; runtime depth limit (e.g. 32) |
| Perf regression on create_flow_run | Benchmark gate; batch persist where applicable |

---

## 15. Decision log

| Decision | Rationale |
| --- | --- |
| M2 always via deployment | Only way to route to arbitrary work pools/workers generically |
| M1 stays in-process | User explicitly chose simplicity for blocking semantics |
| M2 UI = single node + navigate | Avoids Prefect-style nested UI complexity |
| M1 UI = inline mini-DAG | Matches user expectation for blocking visibility |
| Linked inline child flow run (M1) | Enables DAG embedding without losing parent/child observability |
| Recursive nesting in both modes | Required for real-world composition |

---

## 16. Acceptance criteria (feature complete)

- [x] `deployment_ref("x").submit()` enqueues child on child deployment’s work pool and returns awaitable future.
- [x] `wait_for=[subflow_future]` gates downstream tasks correctly.
- [x] Fire-and-forget subflow does not block parent completion.
- [x] `child_flow(...)` blocks in-process; inline DAG visible in UI.
- [x] Subflow chains depth ≥ 3 work across M1/M2 combinations.
- [x] Parent cancel cancels active children.
- [x] All new `perf_matrix` recipes exist (`subflow_lite` / `subflow` presets); compare gates use same `matrix_compare_key`.
- [x] `COMPATIBILITY.md` documents supported subflow subset.
- [x] User guide + nav discoverability (`docs/how-to/subflows.md`).

---

## 17. Next step

**For users:** start at **[How to compose flows with subflows](../how-to/subflows.md)**.

**For maintainers:** stale phased PRs (#28–#33) predate the consolidated #34/#36 landings and can be closed. Follow-ups (if any) should be new tasks: Prefect name-parity experiments, richer parameter validation, or Playwright E2E for DAG double-click navigation — not re-opening Phases 0–5.
