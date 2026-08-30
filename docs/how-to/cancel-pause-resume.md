# How to cancel, pause, and resume flow runs

IronFlow separates **operator lifecycle control** from calendar **gate** waits. This guide covers cancel, pause modes, and resume for the subset shipped today.

Normative limits: **[Compatibility matrix](../compatibility.md)**. Design notes: [flow-run lifecycle plan](https://github.com/PPPSDavid/rust-based-prefect/blob/main/docs/plans/flow-run-lifecycle-control.md) (in-repo).

## Quick reference

| Action | API | Effect |
| --- | --- | --- |
| **Cancel** | `POST /api/flow-runs/{id}/cancel` | Terminal `CANCELLED`. Always **terminate** semantics. |
| **Pause (drain)** | `POST …/pause` `{"mode":"drain"}` | Block new task starts; let in-flight finish; settle `PAUSED`. |
| **Pause (terminate)** | `POST …/pause` `{"mode":"terminate"}` | Cancel RUNNING tasks, kill registered process workers, hold `PAUSED`. |
| **Resume** | `POST …/resume` | Operator pauses only (not gate-only `PAUSED`). |

`mode` is **required** on pause — there is no ambiguous default.

Python plane helpers mirror the HTTP API: `plane.cancel_flow_run(id)`, `plane.pause_flow_run(id, mode="drain"|"terminate")`, `plane.resume_flow_run(id)`. Enum: `InterruptMode.DRAIN` / `InterruptMode.TERMINATE`.

## Cancel

Cancel marks the flow run `CANCELLED`, cancels non-terminal task rows via the task FSM (`interrupt_reason=terminated_by_cancel`), and propagates to active deployment-backed children.

**Process kill:** under `@flow(task_runner=ProcessPoolTaskRunner(...))`, in-flight task bodies run in **registered child processes**. Cancel sends **SIGTERM → grace → SIGKILL** (`IRONFLOW_TASK_TERMINATE_GRACE_SECONDS`, default `2`).

**Thread pool (default):** CPython cannot kill threads. Cancel still fences late `COMPLETED`, but a blind `time.sleep` / CPU loop on a worker thread may continue until the body exits. Prefer `ProcessPoolTaskRunner` when you need hard stop, or poll `assert_flow_not_cancelled` / `sleep_cancelable` in cooperative bodies.

## Pause — drain

Use when you want to **stop scheduling** but finish work already running.

1. In-flight `RUNNING` tasks continue.
2. New `create_task_run` / `submit` is held (`FlowRunSchedulingHeld` in-process).
3. When nothing remains running, the flow settles `PAUSED`.
4. `resume` flips back to `RUNNING`, or **completes** the run if a result was already stored and no non-terminal tasks remain (does not re-enter an exited `@flow()` body).

## Pause — terminate

Use when you want a **hard brake**.

1. Lifecycle is written immediately (new submits held before `PAUSED` settles).
2. RUNNING tasks → `CANCELLED` (late `COMPLETED` fenced).
3. Registered process workers are SIGTERM→SIGKILL’d (same as cancel).
4. Flow holds `PAUSED`.

### Resume after terminate

| Path | Behavior |
| --- | --- |
| **Deployment-backed** | `resume` → `retry` with `resume_from_flow_run_id` (new attempt; P1 skip/recompute rules). |
| **In-process** | `resume` → `prepare_resume` for the **next** `@flow()` invoke; prior attempt is terminalized `CANCELLED` (`superseded_by_terminate_resume`) so it is not left zombie `RUNNING`. |

Interrupted / cancelled-in-flight tasks **re-run**. Eligible `COMPLETED` nodes skip when params + inputs still match (`None` auto or `@task(persist_result=True)`). See **[How to resume tasks and persist results](task-resume-and-persist.md)**.

```python
from prefect_compat import ProcessPoolTaskRunner, flow, task

@task(persist_result=True)
def setup() -> None:
    ...

@task
def work(seconds: float) -> str:
    import time
    time.sleep(seconds)
    return "done"

@flow(task_runner=ProcessPoolTaskRunner(max_workers=2))
def pipeline() -> str:
    setup.submit().result()
    return work.submit(30.0).result()
```

Operator: pause with `mode=terminate` while `work` is running → resume → re-invoke (or deployment retry) → `setup` may skip; `work` re-runs.

## Gate `PAUSED` vs operator pause

| | Gate wait | Operator pause |
| --- | --- | --- |
| How | Temporal `gate(...).submit(until=...)` | `POST …/pause` with `mode` |
| Metadata | No `lifecycle_action=pause` | `lifecycle_action=pause`, `interrupt_mode` set |
| Resume | Gate opens / tick promotes | `POST …/resume` only |

Resume rejects gate-only `PAUSED` runs.

## Logging and context while operating

Use **`get_run_logger()`** / **`get_run_context()`** inside flow/task bodies so cancel/pause investigations show up in **`GET /api/flow-runs/{id}/logs`** and the UI Logs tab. Process-isolated bodies do not inherit ContextVars — log from the coordinating thread when using `ProcessPoolTaskRunner`.

## UI / CLI status

- HTTP API + plane helpers: **shipped**
- UI pause-mode chooser / lifecycle badges: **shipped** (run detail: Pause (drain) / Pause (terminate), Resume for operator pauses only, log search/level/task filters)
- CLI pause helpers: **not shipped**

## Related

- **[Flows](../concepts/flows.md)** — authoring overview
- **[How to choose a task runner](choose-task-runners.md)** — when to use process isolation for hard terminate
- **[How to resume tasks and persist results](task-resume-and-persist.md)** — skip/recompute on resume
- **[How to port a flow from Prefect](port-from-prefect.md)** — Prefect pause/cancel mapping
