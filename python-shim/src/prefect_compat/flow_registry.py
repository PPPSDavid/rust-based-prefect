"""Demo ``@flow`` / ``@task`` callables and the in-process FLOW_REGISTRY.

Kept out of ``server.py`` so workers and the CLI can resolve demo flows without
importing FastAPI.
"""

from __future__ import annotations

from datetime import timedelta

from .cancellation import sleep_cancelable
from .decorators import flow, task, wait
from .gates import gate
from .task_runners import ThreadPoolTaskRunner


@task
def inc(x: int) -> int:
    return x + 1


@task
def dbl(x: int) -> int:
    return x * 2


@task
def passthrough(x: int) -> int:
    return x


@task
def explode(_: int) -> int:
    raise RuntimeError("intentional failure for DAG/state testing")


@task
def after_failure(x: int) -> int:
    return x + 10


@flow(task_runner=ThreadPoolTaskRunner())
def mapped_flow(n: int) -> int:
    first = inc.submit(n)
    mapped_futs = dbl.map(range(n), wait_for=[first])
    wait(mapped_futs)
    return sum(f.result() for f in mapped_futs)


@flow
def chained_flow(n: int) -> int:
    f = passthrough.submit(0)
    for _ in range(n):
        f = inc.submit(f, wait_for=[f])
    return f.result()


@flow
def simple_flow(n: int) -> int:
    # Simple dependency shape: one task depends on one upstream.
    first = inc.submit(n)
    second = dbl.submit(first, wait_for=[first])
    return second.result()


@flow(task_runner=ThreadPoolTaskRunner())
def wide_flow(n: int) -> int:
    # Wide fan-out shape: one upstream gate then many independent mapped tasks.
    first = inc.submit(n)
    mapped_futs = dbl.map(range(n), wait_for=[first])
    wait(mapped_futs)
    return sum(f.result() for f in mapped_futs)


@flow
def long_chain_flow(n: int) -> int:
    # Long dependency chain: strict serial dependence across many tasks.
    f = passthrough.submit(0)
    for _ in range(n):
        f = inc.submit(f, wait_for=[f])
    return f.result()


@task
def sleep_seconds(seconds: float) -> None:
    sleep_cancelable(seconds)


@flow
def gated_flow(n: int) -> int:
    """Demo flow with a temporal gate between prep and downstream work."""
    first = inc.submit(n)
    gf = gate(name="demo-gate").submit(after=timedelta(seconds=0), wait_for=[first])
    return dbl.submit(first, wait_for=[gf]).result()


@flow
def cancelable_flow(n: int, sleep_duration: float = 10.0) -> int:
    """Multi-task flow for cancel/retry UI tests: fast task, long sleep, downstream task."""
    first = inc.submit(n)
    slept = sleep_seconds.submit(sleep_duration, wait_for=[first])
    second = dbl.submit(first, wait_for=[slept])
    return second.result()


@task
def slow_dbl(x: int) -> int:
    sleep_cancelable(2.2)
    return x * 2


@flow(task_runner=ThreadPoolTaskRunner())
def slow_wide_flow(n: int = 8) -> int:
    """UI demo: mapped fan-out stays RUNNING long enough to film live DAG updates."""
    first = inc.submit(n)
    mapped_futs = slow_dbl.map(range(n), wait_for=[first])
    wait(mapped_futs)
    return sum(f.result() for f in mapped_futs)


@flow
def failing_flow(n: int) -> int:
    first = inc.submit(n)
    bad = explode.submit(first, wait_for=[first])
    # This node should be unreachable once upstream fails.
    final = after_failure.submit(bad, wait_for=[bad])
    return final.result()


@flow(task_runner=ThreadPoolTaskRunner())
def wait_all_ok_flow(n: int) -> int:
    """All concurrent submits succeed; wait_all resolves COMPLETED."""
    a = inc.submit(n)
    b = dbl.submit(n)
    wait([a, b])
    return a.result() + b.result()


@flow(task_runner=ThreadPoolTaskRunner())
def wait_all_orphan_fail_flow(n: int) -> str:
    """Unobserved failed submit; wait_all must mark the flow FAILED."""
    explode.submit(n)
    return "unreachable"


@flow
def wait_all_inline_subflow(n: int) -> int:
    """Inline subflow success under wait_all aggregation."""
    return simple_flow(n)


@flow(task_runner=ThreadPoolTaskRunner())
def detach_orphan_fail_flow(n: int) -> int:
    """Detached failed submit must not fail the parent under wait_all."""
    explode.submit(n, detach=True)
    return dbl.submit(n).result()


@flow(final_state="explicit", task_runner=ThreadPoolTaskRunner())
def explicit_orphan_fail_flow(n: int) -> int:
    """Body-driven completion: unobserved boom stays FAILED while flow COMPLETED."""
    explode.submit(n)
    return dbl.submit(n).result()


@task
def setup() -> None:
    return None


@task(persist_result=True)
def expensive(x: int) -> dict:
    return {"x": x, "n": 42, "items": [1, 2, 3]}


@task
def volatile(x: int) -> int:
    return x + 1


@flow(name="persist_result_demo")
def persist_result_demo_flow(n: int = 7) -> int:
    """Seed flow for UI e2e: None marker + JSON-safe persist_result payload."""
    setup.submit()
    payload = expensive.submit(n)
    return volatile.submit(payload.result()["n"]).result()


FLOW_REGISTRY = {
    "simple_flow": simple_flow,
    "wide_flow": wide_flow,
    "long_chain_flow": long_chain_flow,
    "mapped_flow": mapped_flow,
    "chained_flow": chained_flow,
    "failing_flow": failing_flow,
    "cancelable_flow": cancelable_flow,
    "slow_wide_flow": slow_wide_flow,
    "gated_flow": gated_flow,
    "wait_all_ok_flow": wait_all_ok_flow,
    "wait_all_orphan_fail_flow": wait_all_orphan_fail_flow,
    "wait_all_inline_subflow": wait_all_inline_subflow,
    "detach_orphan_fail_flow": detach_orphan_fail_flow,
    "explicit_orphan_fail_flow": explicit_orphan_fail_flow,
    "persist_result_demo": persist_result_demo_flow,
}

BENCHMARK_FLOW_MAP = {
    "simple": simple_flow,
    "wide": wide_flow,
    "long_chain": long_chain_flow,
    "failing": failing_flow,
    "gated": gated_flow,
    "wait_all_ok": wait_all_ok_flow,
    "wait_all_orphan_fail": wait_all_orphan_fail_flow,
    "wait_all_inline_subflow": wait_all_inline_subflow,
    "detach_orphan_fail": detach_orphan_fail_flow,
    "explicit_orphan_fail": explicit_orphan_fail_flow,
    "persist_result": persist_result_demo_flow,
    # Backwards-compatible aliases for existing scripts.
    "mapped": wide_flow,
    "chained": long_chain_flow,
}

BENCHMARK_FLAVOR_HELP = (
    "Unsupported flavor. Use one of: simple, wide, long_chain, failing, "
    "gated, wait_all_ok, wait_all_orphan_fail, wait_all_inline_subflow, "
    "detach_orphan_fail, explicit_orphan_fail, persist_result"
)
