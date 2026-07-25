# How to compose flows with subflows

FlowOxide supports **two** ways to nest flows (also called **nested flows**). If you are coming from Prefect’s `run_deployment` / subflow APIs, start here—FlowOxide’s surface is smaller and intentional.

Pick the mechanism that matches where the child must run.

| Goal | Mechanism | What to write |
| --- | --- | --- |
| Run a child **in the same process** and wait for its return value | **Inline (M1)** | Call the child `@flow` like a normal function: `child(...)` |
| Run a child on a **deployment / work pool** (possibly another worker) | **Deployment-backed (M2)** | `deployment_ref("name").submit(...).result()` |

Normative limits live in the **[Compatibility matrix](../compatibility.md)**. Conceptual background: **[Flows](../concepts/flows.md)** · Prefect mapping: **[Prefect → FlowOxide](../PREFECT_FLOWOXIDE_MAPPING.md)**.

## Prerequisites

- `from prefect_compat import flow, task, set_control_plane, InMemoryControlPlane, deployment_ref, wait`
- A control plane registered with **`set_control_plane`** (in-process or via the self-hosted server).
- For **M2 only:** a **deployment** whose `flow_name` matches the child flow, and a **worker** claiming that deployment’s work pool (local worker thread or `flowoxide worker start`). See **[How to create and update deployments](deployments.md)** and **[How to deploy with the CLI](deploy-with-cli.md)**.

## Mechanism 1 — Blocking inline

Call another `@flow` from inside a parent flow. The parent **blocks** until the child returns. FlowOxide records a **linked child flow run** (`execution_mode=inline`) with parent/root/depth metadata.

```python
from prefect_compat import flow, set_control_plane, InMemoryControlPlane, task

set_control_plane(InMemoryControlPlane())

@task
def add_one(n: int) -> int:
    return n + 1

@flow
def child_inline(n: int) -> int:
    return add_one.submit(n).result()

@flow
def parent_inline() -> int:
    # Mechanism 1: direct call — same process, linked child run
    return child_inline(10)
```

**When to use:** shared process, low latency, no separate work pool. Nesting works: an inline child may call further inline children or submit deployment-backed subflows.

**In the UI:** open the parent run → **DAG** tab. Inline children appear as **`inline_subflow`** nodes (dashed border). The run detail page lists **child flow runs**; open a child for its own DAG.

## Mechanism 2 — Deployment-backed subflow as task

Resolve a deployment by **unique name** (or UUID) and **`submit`**. FlowOxide creates a surrogate parent task (`kind=subflow`), enqueues a **deployment run**, and returns a **`SubflowFuture`**.

```python
from prefect_compat import (
    flow,
    set_control_plane,
    InMemoryControlPlane,
    deployment_ref,
    wait,
)

plane = InMemoryControlPlane()
set_control_plane(plane)

@flow
def child_deploy(n: int = 0) -> int:
    return 42 + n

# Register the callable for a local worker, then create a deployment once.
# plane.create_deployment(name="child-deploy", flow_name="child_deploy", ...)
# Start a worker that claims that deployment's work pool.

@flow
def parent_deploy() -> int:
    fut = deployment_ref("child-deploy").submit(n=1)
    return fut.result()  # wait for child deployment run + flow result
```

### Wait for a subflow, then continue

`wait_for` accepts **`SubflowFuture`** alongside ordinary task futures:

```python
@flow
def parent_with_downstream() -> int:
    fut = deployment_ref("child-deploy").submit(n=3)
    # Block until the subflow finishes before this task runs
    out = add_one.submit(fut, wait_for=[fut]).result()
    return out
```

You can also `wait([fut])` explicitly.

### Fire-and-forget

Under the default **`final_state="wait_all"`**, an undeclared `submit()` still counts toward parent completion. Opt out with **`detach=True`** (or `@flow(final_state="explicit")`):

```python
@flow
def parent_fire_and_forget() -> str:
    deployment_ref("child-deploy").submit(n=1, detach=True)
    return "parent_done"  # returns without waiting for the child
```

The child still runs when a worker claims the deployment run.

**When to use:** child must run on another pool/worker, or you want enqueue + claim semantics. Nested M2 works: a child deployment flow may `deployment_ref(...).submit(...)` further children (multi-worker recursive chains are supported).

**In the UI:** parent DAG shows a **`subflow_task`** node (solid border / gold styling). Child flow runs appear under the parent’s **Child flow runs** list with `execution_mode=deployment`.

## Choosing M1 vs M2

| Question | Prefer |
| --- | --- |
| Same process OK? | **M1** inline |
| Need a different work pool / worker? | **M2** `deployment_ref` |
| Need `wait_for` / fire-and-forget on the parent graph? | **M2** (surrogate task) |
| Lowest latency nesting? | **M1** |

You can **mix** both in one parent (inline call + deployment submit).

## Cancel propagation

Cancelling a parent flow run cancels **active** deployment-backed children linked to that parent. Inline children in the same process observe cancellation through the shared control plane. Details: **[Compatibility matrix](../compatibility.md)**.

## Common mistakes

| Symptom | Likely cause |
| --- | --- |
| `deployment_ref(...).submit()` outside a `@flow` | Submit requires an **active parent flow run**. |
| Submit hangs forever | No worker claiming the child’s work pool, or deployment missing/paused. |
| `deployment not found` | Create the deployment (API/CLI) with the exact name you pass to `deployment_ref`. |
| Sibling unlinked runs when calling `child(...)` | Ensure you use `@flow` on the child and a shared control plane; FlowOxide links inline children automatically when called from an active parent flow. |

## Related pages

- **[Flows](../concepts/flows.md)** — `@flow`, hooks, and subflow overview.
- **[DAG and forecast](../concepts/dag-and-forecast.md)** — `inline_subflow` / `subflow_task` node kinds.
- **[Deployments](deployments.md)** · **[Deploy with CLI](deploy-with-cli.md)** — create child deployments and workers.
- **[Port from Prefect](port-from-prefect.md)** — import and subset checklist.
- **[Compatibility matrix](../compatibility.md)** — supported subset and out-of-scope items.
