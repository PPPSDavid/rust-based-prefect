"""
A/B script: Prefect ``wide`` workload with default (thread-pool) vs sequential task runner.

Plan item: prefect-sequential-ab — splits parallelism vs orchestration overhead.

Usage (from repo root, with ``prefect`` installed)::

    python benchmarks/prefect_wide_runner_ab.py
"""

from __future__ import annotations

import statistics
import time
from typing import Callable


def _timed(label: str, fn: Callable[[], object], repeats: int = 3) -> None:
    durations: list[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        durations.append(time.perf_counter() - t0)
    med = statistics.median(durations)
    print(f"{label}: median={med:.4f}s over {repeats} runs")


def main() -> None:
    try:
        from prefect import flow, task  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"prefect not importable: {exc}") from exc

    try:
        from prefect.task_runners import SequentialTaskRunner, ThreadPoolTaskRunner  # type: ignore
    except Exception:
        try:
            from prefect import SequentialTaskRunner, ThreadPoolTaskRunner  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise SystemExit(f"Could not import Prefect task runners: {exc}") from exc

    n = 100

    @task
    def inc(x: int) -> int:
        return x + 1

    @task
    def dbl(x: int) -> int:
        return x * 2

    @flow(task_runner=ThreadPoolTaskRunner())
    def wide_thread(_: int) -> int:
        first = inc.submit(n)
        mapped_futs = dbl.map(range(n), wait_for=[first])
        return sum(f.result() for f in mapped_futs)

    @flow(task_runner=SequentialTaskRunner())
    def wide_seq(_: int) -> int:
        first = inc.submit(n)
        mapped_futs = dbl.map(range(n), wait_for=[first])
        return sum(f.result() for f in mapped_futs)

    _timed("prefect wide ThreadPoolTaskRunner", lambda: wide_thread(0))
    _timed("prefect wide SequentialTaskRunner", lambda: wide_seq(0))


if __name__ == "__main__":
    main()
