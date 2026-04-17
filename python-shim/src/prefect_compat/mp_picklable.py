"""Top-level callables for process-pool ``task.map`` — must live in ``prefect_compat`` so multiprocessing can unpickle by import path."""

from __future__ import annotations


def inc(x: int) -> int:
    return x + 1
