"""Top-level callables for process-pool ``task.map`` / ``submit``.

Must live in ``prefect_compat`` so multiprocessing can unpickle by import path.
"""

from __future__ import annotations

import time


def inc(x: int) -> int:
    return x + 1


def sleep_ms(ms: int) -> int:
    time.sleep(ms / 1000.0)
    return ms


def const_one() -> int:
    return 1


def raise_value_error(msg: str = "submit boom") -> None:
    raise ValueError(msg)
