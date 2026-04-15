from __future__ import annotations

from prefect import flow, task


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
    return aggregate.submit([f.result() for f in mapped], wait_for=mapped).result()


if __name__ == "__main__":
    print(f"prefect_result={example_flow(5)}")
