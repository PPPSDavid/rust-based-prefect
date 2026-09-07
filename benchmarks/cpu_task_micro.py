"""CPU-bound ``@flow`` samples for GIL vs free-threaded runner comparison."""

from __future__ import annotations

from collections.abc import Callable

from prefect_compat import flow, task, wait
from prefect_compat.mp_picklable import cpu_burn as _cpu
from prefect_compat.task_runners import ProcessPoolTaskRunner, ThreadPoolTaskRunner

from benchmarks._task_cast import as_task_wrapper


def decorator_cpu_flow(
    *,
    width: int,
    mode: str,
    runner_kind: str,
) -> Callable[[], int]:
    """Build a flow that maps or submits ``cpu_burn`` over ``range(width)``."""
    work = as_task_wrapper(task(_cpu))
    mx = max(2, min(8, int(width)))
    if runner_kind == "process":
        runner: ProcessPoolTaskRunner | ThreadPoolTaskRunner = ProcessPoolTaskRunner(
            max_workers=mx
        )
    else:
        runner = ThreadPoolTaskRunner(max_workers=mx)

    if mode == "submit":

        @flow(task_runner=runner)
        def submit_sample() -> int:
            futs = [work.submit(i) for i in range(width)]
            wait(futs)
            return sum(f.result() for f in futs)

        return submit_sample

    @flow(task_runner=runner)
    def map_sample() -> int:
        futs = work.map(range(width))
        wait(futs)
        return sum(f.result() for f in futs)

    return map_sample
