# How to use concurrency limits

IronFlow supports Prefect-style **global concurrency limits** (named slots) and **tag-based task limits**. Both share one Rust-backed SQLite slot ledger. Deployment concurrency (`concurrency_limit` on a deployment) is separate — it only caps concurrent **deployment runs**.

Normative scope: **[Compatibility matrix](../compatibility.md)** · Design notes: **[`docs/plans/concurrency-limits.md`](../plans/concurrency-limits.md)** · Concepts: **[Tasks](../concepts/tasks.md)**.

Upstream reference: [Prefect global concurrency limits](https://docs.prefect.io/v3/concepts/global-concurrency-limits), [tag-based concurrency limits](https://docs.prefect.io/v3/concepts/tag-based-concurrency-limits).

## Global limit + `concurrency` context manager

```python
from prefect_compat import (
    concurrency,
    create_concurrency_limit,
    flow,
    set_control_plane,
    task,
)
from prefect_compat.runtime import InMemoryControlPlane

plane = InMemoryControlPlane(history_path="data/demo.jsonl")
set_control_plane(plane)
create_concurrency_limit("database", limit=5)

@task
def query(sql: str) -> str:
    with concurrency("database", occupy=1):
        # at most 5 concurrent holders of "database"
        return sql

@flow
def run() -> list[str]:
    return [query.submit(f"SELECT {i}").result() for i in range(3)]
```

**Behavior:**

| Case | Default (`strict=False`) | `strict=True` |
| --- | --- | --- |
| Limit missing / inactive | Warn and proceed (no slots) | Raise `ConcurrencyLimitError` |
| No free slots | Block / retry until available | Same |
| `timeout_seconds` elapses | Raise `ConcurrencySlotTimeoutError` | Same |

Slots are **leased** (default 300s) and released when the `with` block exits. Expired leases are reclaimed on the scheduler maintenance tick.

## Rate limiting (`rate_limit`)

Configure `slot_decay_per_second` so slots free over time. `rate_limit` acquires in rate-limit mode and does not need an explicit release.

```python
create_concurrency_limit("api", limit=10, slot_decay_per_second=2.0)

@task
def call_api() -> None:
    rate_limit("api")  # from prefect_compat
    ...
```

If decay is unset, `rate_limit` raises `ConcurrencyLimitError`.

## Tag-based task limits

Tags are stored as global limits named `tag:{tag}`. They gate entry to **`Running`** (AND across tags). Limit `0` aborts the task (`CANCELLED` + `ConcurrencyLimitError`).

```python
from prefect_compat import create_tag_concurrency_limit, flow, task
from prefect_compat.task_runners import ThreadPoolTaskRunner

create_tag_concurrency_limit("db", limit=2)

@task(tags=["db"])
def write_row(n: int) -> int:
    return n

@flow(task_runner=ThreadPoolTaskRunner(max_workers=8))
def fanout() -> list[int]:
    return [f.result() for f in write_row.map(list(range(10)))]
```

With a limit of 2, at most two tagged task runs hold slots (and are `RUNNING`) at once under `map()`, even when the thread pool is larger.

## HTTP admin API

| Method | Path |
| --- | --- |
| `GET` | `/api/concurrency-limits` |
| `POST` | `/api/concurrency-limits` body `{name, limit, slot_decay_per_second?, active?}` |
| `GET` | `/api/concurrency-limits/{name}` |
| `PATCH` | `/api/concurrency-limits/{name}` |
| `DELETE` | `/api/concurrency-limits/{name}` |

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `IRONFLOW_TASK_TAG_SLOT_WAIT_SECONDS` | `1.0` | Poll interval while waiting for tag slots |

## Relation to deployment concurrency

| Mechanism | Gates |
| --- | --- |
| Deployment `concurrency_limit` + `ENQUEUE` / `CANCEL_NEW` | Concurrent runs **of that deployment** |
| Global / tag GCL | Named slots for **any Python code** / tagged **task runs** |

## Performance check

```bash
uv run python benchmarks/perf_matrix.py run --preset gcl --repetitions 1 --warmups 0 --jobs 1 \
  --out-json /tmp/gcl.json --out-md /tmp/gcl.md
```
