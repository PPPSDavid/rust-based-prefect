# Execution contract

IronFlow separates **what the flow author declared** from **what the runtime can prove** about graph shape. That contract governs resume skips on flow-run retry.

See also: **[State transition matrix](state-transition-matrix.md)**, **[How to choose graph mode and retry](../how-to/graph-mode-and-retry.md)**, **[Task resume and persist](../how-to/task-resume-and-persist.md)**.

## Graph mode resolution

Every `@flow` run stores two modes:

| Field | Meaning |
| --- | --- |
| `declared_graph_mode` | Author intent from `@flow(graph_mode=…)` — default **`auto`**. |
| `effective_graph_mode` | Runtime policy after planner compile — **`static`** or **`dynamic`**. |

Resolution (after static-planner compile):

```text
declared = graph_mode from decorator (default "auto")

if declared == "dynamic":
    effective = dynamic
elif declared == "static":
    if fallback_required or empty manifest → FAIL at start
    effective = static
else:  # auto
    effective = static if (not fallback_required and manifest non-empty) else dynamic
```

Persisted on the flow run: `declared_graph_mode`, `effective_graph_mode`, `manifest_fingerprint`, `contract_mismatch`, `flow_attempt_number`.

## Static (effective) — frozen contract

When **`effective=static`**:

1. **Snapshot** — `manifest_fingerprint` is stored on the lineage root at first static run.
2. **Runtime guard** — `next_planned_node_id` must not allocate `dyn_*` nodes. If the manifest is exhausted, static map fan-out reuses the last manifest slot; otherwise the run **fails** with `StaticGraphContractViolation`.
3. **Retry** — resume skips apply only when:
   - parameters fingerprint matches the prior attempt, and
   - manifest fingerprint matches the lineage root, and
   - the node is resume-eligible (`None` auto-skip or `@task(persist_result=True)` JSON-safe).

If the manifest fingerprint changes on retry (code edit), **`contract_mismatch=true`** and resume skips are disabled (full re-execute).

## Dynamic (effective) — always fresh

When **`effective=dynamic`**:

- `resume_skips_enabled=false` always.
- Retry creates a new flow run and **re-executes all tasks** — no cross-attempt task reuse assumptions.

Use **`graph_mode="dynamic"`** when you know runtime branches the planner cannot see, or when you prefer simplicity over skip optimization.

## Safeguards

| Situation | Behavior |
| --- | --- |
| User **`static`** + planner `fallback_required` | **Fail at start** (`StaticGraphDeclarationError`) |
| User **`static`** or **auto→static** + runtime needs `dyn_*` | **Fail run** (`StaticGraphContractViolation`) |
| User **`dynamic`** + planner says static | **Honor user** — effective dynamic, no skips |
| **auto→static** + manifest change on retry | Contract mismatch → disable skips |
| **auto→dynamic** | Never skip |

Auto-detect is **reliable for “not static”** (dynamic control flow, empty manifest). It **cannot prove static** without runtime guards — optimistic static always keeps the `dyn_*` fail-fast check.

## API surface

- Decorator: `@flow(graph_mode="auto"|"static"|"dynamic")` (default `"auto"`).
- Flow run detail: `declared_graph_mode`, `effective_graph_mode`, `manifest_fingerprint`, `contract_mismatch`, `flow_attempt_number`.
- Implementation: `python-shim/src/prefect_compat/graph_mode.py`, `configure_flow_graph_mode()` in `runtime.py`.
