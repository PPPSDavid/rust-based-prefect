from uuid import uuid4

from prefect_compat import InMemoryControlPlane, RunState, flow, set_control_plane, task, wait


def test_submit_chain_and_map():
    plane = InMemoryControlPlane()
    set_control_plane(plane)

    @task
    def inc(x: int) -> int:
        return x + 1

    @task
    def dbl(x: int) -> int:
        return x * 2

    @flow
    def sample() -> list[int]:
        xs = [f.result() for f in inc.map([1, 2, 3])]
        ys = [dbl.submit(x).result() for x in xs]
        return ys

    out = sample()
    assert out == [4, 6, 8]
    assert len(plane.events()) >= 3


def test_duplicate_transition_token_is_idempotent():
    plane = InMemoryControlPlane()
    run = plane.create_flow_run("x")
    token = uuid4()

    first = plane.set_flow_state(run.run_id, RunState.PENDING, token, "propose", 0)
    second = plane.set_flow_state(run.run_id, RunState.PENDING, token, "propose", 0)

    assert first.status == "applied"
    assert second.status == "duplicate"
    assert len(plane.events()) == 1


def test_wait_for_dependency_order():
    plane = InMemoryControlPlane()
    set_control_plane(plane)
    calls: list[str] = []

    @task
    def first() -> int:
        calls.append("first")
        return 1

    @task
    def second(x: int) -> int:
        calls.append("second")
        return x + 1

    @flow
    def pipeline() -> int:
        f1 = first.submit()
        f2 = second.submit(f1, wait_for=[f1])
        wait([f1, f2])
        return f2.result()

    assert pipeline() == 2
    assert calls == ["first", "second"]
