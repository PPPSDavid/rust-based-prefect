from __future__ import annotations

import os

from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task, wait


@task
def start(n: int) -> int:
    return n + 1


@task
def process(n: int) -> int:
    return n * 2


@task
def aggregate(values: list[int]) -> int:
    return sum(values)


@flow
def example_flow(total: int = 10) -> int:
    first = start.submit(total)
    mapped = process.map([first.result(), first.result() + 1], wait_for=[first])
    wait(mapped)
    return aggregate.submit([f.result() for f in mapped], wait_for=mapped).result()


def run() -> tuple[int, int]:
    plane = InMemoryControlPlane(history_path=os.environ.get("IRONFLOW_HISTORY_PATH"))
    set_control_plane(plane)
    result = example_flow(5)
    return result, len(plane.events())


if __name__ == "__main__":
    value, event_count = run()
    print(f"ironflow_result={value}")
    print(f"ironflow_events={event_count}")
