"""Pluggable task runners for Ironflow (Prefect-shaped API)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class MapTaskRunner(Protocol):
    """Runner types that implement ``map`` via ``map_values``."""

    def map_values(
        self,
        wrapper: Any,
        values: list[Any],
        wait_for: list[Any] | None,
        wait_fn: Callable[..., Any],
        submit_fn: Callable[[Any], Any],
    ) -> list[Any]:
        ...


@dataclass
class SequentialTaskRunner:
    """Single-threaded map: deterministic in-process ``submit`` chain."""

    def map_values(
        self,
        wrapper: Any,
        values: list[Any],
        wait_for: list[Any] | None,
        wait_fn: Callable[..., Any],
        submit_fn: Callable[[Any], Any],
    ) -> list[Any]:
        if wait_for:
            wait_fn(wait_for)
        return [submit_fn(v) for v in values]


@dataclass
class ThreadPoolTaskRunner:
    """Concurrent ``map`` using a thread pool (Prefect 3 default style)."""

    max_workers: int | None = None

    def resolve_max_workers(self) -> int:
        if self.max_workers is not None:
            return max(1, self.max_workers)
        env = os.environ.get("IRONFLOW_TASK_RUNNER_THREAD_POOL_MAX_WORKERS")
        if env and env.strip().isdigit():
            return max(1, int(env.strip()))
        return max(1, min(32, (os.cpu_count() or 1) + 4))

    def map_values(
        self,
        wrapper: Any,
        values: list[Any],
        wait_for: list[Any] | None,
        wait_fn: Callable[..., Any],
        submit_fn: Callable[[Any], Any],
    ) -> list[Any]:
        from concurrent.futures import ThreadPoolExecutor

        if wait_for:
            wait_fn(wait_for)
        if not values:
            return []
        mx = self.resolve_max_workers()
        if mx <= 1 or len(values) == 1:
            return [submit_fn(v) for v in values]

        with ThreadPoolExecutor(max_workers=mx) as pool:
            return list(pool.map(submit_fn, values))


@dataclass
class ProcessPoolTaskRunner:
    """Marker for process-pool ``map`` (handled in ``TaskWrapper`` — task callable must be picklable)."""

    max_workers: int | None = None

    def resolve_max_workers(self) -> int:
        if self.max_workers is not None:
            return max(1, self.max_workers)
        env = os.environ.get("IRONFLOW_TASK_RUNNER_PROCESS_POOL_MAX_WORKERS")
        if env and env.strip().isdigit():
            return max(1, int(env.strip()))
        return max(1, os.cpu_count() or 1)


def default_task_runner_from_env() -> MapTaskRunner | ProcessPoolTaskRunner:
    kind = os.environ.get("IRONFLOW_TASK_RUNNER", "thread").lower().strip()
    if kind in ("sequential", "seq", "serial"):
        return SequentialTaskRunner()
    if kind in ("process", "multiprocessing", "mp"):
        return ProcessPoolTaskRunner()
    return ThreadPoolTaskRunner()
