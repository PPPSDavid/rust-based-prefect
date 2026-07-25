# Transition hooks priority (X → Y → do Z)

**Status:** Priority polish/expand — **core already shipped**  
**Canvas ID:** **PH**  
**Last updated:** 2026-07-25  
**Ownership:** `python-shim/` (+ docs); engine only if new states need hook edges  

---

## 1. Already supported (do not reimplement)

IronFlow already lets you define custom hooks **alongside** `@flow` / `@task` in the Python file:

```python
from prefect_compat import RunState, flow, on_transition, task

def notify_failure(ctx):
    # ctx: TransitionContext — flow_run_id, from_state, to_state, task_run_id, …
    send_alert(ctx)

@task(
    transition_hooks=[
        on_transition(notify_failure, to_state=RunState.FAILED),
        on_transition(
            log_done,
            from_state=RunState.RUNNING,
            to_state=RunState.COMPLETED,
        ),
    ]
)
def work():
    ...

@flow(transition_hooks=[on_transition(on_any_terminal, to_state=RunState.CANCELLED)])
def pipeline():
    work.submit()
```

Semantics (normative: `COMPATIBILITY.md`):

- Match optional `from_state` / `to_state` (`None` = wildcard).
- Run **synchronously in-process** after a successful control-plane transition.
- Do **not** hold the control-plane lock; exceptions are logged and do not fail the run.
- This is an IronFlow extension (not Prefect’s `on_running` / `on_failure` kwarg names).

Code: `python-shim/src/prefect_compat/hooks.py`, wired in `decorators.py`. Tests: `python-shim/tests/test_transition_hooks.py`.

---

## 2. Why a priority track anyway

Review asked for “if state X → Y, do Z, definable on the flow/task.” That product intent is **met for the in-process path**, but:

1. **Discoverability** — easy to miss vs Prefect named hooks; only concepts/COMPATIBILITY snippets, no dedicated how-to.
2. **Ergonomics** — `transition_hooks=[on_transition(...)]` is correct but verbose; decorator sugar / thin Prefect-shaped aliases would match author expectations.
3. **Coverage gaps** — verify/fix hooks under **process-pool** workers, **deployment HTTP workers** (entrypoint load), and new **lifecycle** edges (operator pause/cancel from P3.2).
4. **Docs/examples** — seed a real “alert on FAILED” sample in quickstart/port guide.

---

## 3. Session backlog

| ID | Action | Acceptance |
| --- | --- | --- |
| **PH.0** | Dedicated how-to + port-guide row + example script; point from concepts | User can copy-paste X→Y→Z on `@flow`/`@task` without reading COMPATIBILITY |
| **PH.1** | Ergonomic sugar (pick one, keep existing API): e.g. `@task(on_failure=fn)` / `@task(on_completion=fn)` as thin wrappers to `on_transition`, and/or stackable `@on_transition(...)` decorator | Same semantics; tests; COMPATIBILITY lists both styles |
| **PH.2** | Worker/path audit: thread vs process-pool vs `ironflow worker` / HTTP claim — hooks fire when expected; document if hooks only run in the process that applies the transition | Matrix in how-to; fix holes or document limits |
| **PH.3** | Lifecycle edges: hooks see operator pause/cancel/resume transitions once P3.2 lands; examples for `→CANCELLED`, `→PAUSED` | Tests with new lifecycle modes |
| **PH.4** (optional) | Async hook scheduling (**not** blocking the transition path) — queue to thread/executor; still no control-plane lock | Design note first; default remains sync |

---

## 4. Explicit non-goals

- Full Prefect hook API surface (`on_running`, `on_crashed`, …) as a hard dependency — aliases only
- Hooks that mutate/veto transitions (observe-after-commit stays the model unless a separate design lands)
- Cloud/webhook automation engine (that’s P7); hooks are in-process side effects
- Claiming hooks run in a different machine than the worker that executed the transition without shipping that design

---

## 5. Suggested order

`PH.0` (docs) can start anytime. `PH.1` after or with PH.0. `PH.2` before calling hooks “production ready” for Compose workers. `PH.3` after P3.2a+ state edges exist.
