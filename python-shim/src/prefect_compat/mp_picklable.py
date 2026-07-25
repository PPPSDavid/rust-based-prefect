"""Top-level callables for process-pool ``task.map`` — must live in ``prefect_compat`` so multiprocessing can unpickle by import path."""

from __future__ import annotations

import time


def inc(x: int) -> int:
    return x + 1


def blind_sleep(seconds: float) -> str:
    """Block without polling cancel — used to prove process terminate/kill."""
    time.sleep(float(seconds))
    return "awake"


def return_none(_: int) -> None:
    return None
