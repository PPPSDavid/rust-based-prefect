"""One-off profiler for ironflow inproc hot paths (plan: profile-hot-path)."""
from __future__ import annotations

import cProfile
import os
import pstats
import sys
import tempfile
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    os.chdir(tempfile.mkdtemp())
    sys.path.insert(0, str(ROOT / "python-shim" / "src"))
    from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task, wait
    from prefect_compat.task_runners import ThreadPoolTaskRunner

    plane = InMemoryControlPlane()
    set_control_plane(plane)

    @task
    def inc(x: int) -> int:
        return x + 1

    @task
    def dbl(x: int) -> int:
        return x * 2

    @task
    def passthrough(x: int) -> int:
        return x

    @flow(task_runner=ThreadPoolTaskRunner())
    def wide(n: int) -> int:
        first = inc.submit(n)
        fs = dbl.map(range(n), wait_for=[first])
        wait(fs)
        return sum(f.result() for f in fs)

    @flow
    def chain(n: int) -> int:
        f = passthrough.submit(0)
        for _ in range(n):
            f = inc.submit(f, wait_for=[f])
        return f.result()

    pr = cProfile.Profile()
    pr.enable()
    wide(100)
    chain(100)
    pr.disable()

    s = StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumtime")
    ps.print_stats(25)
    print(s.getvalue())


if __name__ == "__main__":
    main()
