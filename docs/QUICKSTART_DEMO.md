# Quick start: run a demo flow

This page walks through **one minimal flow** in a few minutes: no API server and no UI required. Complete **[Installation](INSTALL.md)** first (clone, Python env, `cargo build`), then continue here.

## What the demo looks like (source)

Imports use **`prefect_compat`**, not `prefect`. Tasks are plain functions decorated with **`@task`**; the workflow is a function decorated with **`@flow`**. The control plane is an **`InMemoryControlPlane`** registered with **`set_control_plane`** before the flow runs.

**Flow of data:** `example_flow(5)` calls **`start`** once (`5 → 6`), then **`process.map`** runs **`process`** on **two** values derived from that result (`6` and `7` → `12` and `14`). **`aggregate`** sums those to **`26`**.

The listing below is the same source as `python-shim/examples/flow_ironflow.py` (embedded here so you can read it without opening the repo):

```python
--8<-- "python-shim/examples/flow_ironflow.py"
```

- **`start.submit(total)`** — schedule one task; **`first.result()`** waits for it inside the flow.
- **`process.map(..., wait_for=[first])`** — fan-out; **`wait(mapped)`** waits for all mapped futures.
- **`aggregate.submit(..., wait_for=mapped)`** — downstream task depending on the mapped set.
- **`run()`** — creates the control plane, sets it globally, runs the flow, returns `(result, event_count)`.

## 1. Prepare the environment

From the **root** of a cloned IronFlow repository (with dependencies installed as in the README):

**Windows (PowerShell):**

```powershell
$env:PYTHONPATH = "python-shim/src"
```

**macOS / Linux:**

```bash
export PYTHONPATH=python-shim/src
```

That lets Python import the `prefect_compat` package without a separate editable install.

## 2. Run the bundled example

Still at the repo root:

```bash
python python-shim/examples/flow_ironflow.py
```

## 3. What you should see

Typical **stdout** looks like:

```text
ironflow_result=26
ironflow_events=15
```

| Line | Meaning |
| --- | --- |
| `ironflow_result=26` | The flow returned **26**. The example uses `submit`, `map`, and `aggregate`: starting from `5`, it computes a small DAG and sums the mapped results (here \(12 + 14 = 26\)). |
| `ironflow_events=15` | The in-memory control plane recorded **15** append-only events for this run (flow/task lifecycle transitions and related records). The exact count can vary slightly with version, but it should be **stable** for the same code and parameters. |

Nothing listens on a port: orchestration runs **in-process**. If you set `IRONFLOW_HISTORY_PATH` to a file path before running, the same flow also **persists** history to that JSONL file (see the repository README for persistence defaults).

## 4. What this proves

- You can author with **`@flow` / `@task`** from **`prefect_compat`**.
- **`submit`**, **`map`**, and **`wait`** work together on a tiny workload.
- The control plane records a **structured event history** you can inspect (`len(plane.events())` in code, or on-disk history when configured).

## 5. Next steps

- **[Prefect → IronFlow](PREFECT_IRONFLOW_MAPPING.md)** — map Prefect concepts to this project.
- **[Compatibility](compatibility.md)** — what is supported vs not.
- **[How to set up IronFlow](how-to/setup.md)** · **[How to run the server and UI](how-to/server-and-ui.md)** — optional stack beyond in-process runs.
- **README** in the repo — optional HTTP API, UI, and `cargo build` for the Rust engine.
