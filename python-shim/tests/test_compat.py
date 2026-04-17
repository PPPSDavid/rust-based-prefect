from uuid import uuid4

from prefect_compat import InMemoryControlPlane, RunState, flow, set_control_plane, task, wait


def test_submit_chain_and_map(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "chain.jsonl"))
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


def test_duplicate_transition_token_is_idempotent(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "dup.jsonl"))
    run = plane.create_flow_run("x")
    token = uuid4()

    first = plane.set_flow_state(run.run_id, RunState.PENDING, token, "propose", 0)
    second = plane.set_flow_state(run.run_id, RunState.PENDING, token, "propose", 0)

    assert first.status == "applied"
    assert second.status == "duplicate"
    assert len(plane.events()) == 1


def test_wait_for_dependency_order(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "wait.jsonl"))
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


def test_flow_runs_without_server_or_ui(tmp_path):
    history = tmp_path / "script_run_history.jsonl"
    plane = InMemoryControlPlane(history_path=str(history))
    set_control_plane(plane)

    @task
    def inc(x: int) -> int:
        return x + 1

    @flow
    def script_like_flow() -> int:
        return inc.submit(41).result()

    assert script_like_flow() == 42
    summary = plane.summary()
    assert summary["flow_runs"] == 1
    assert summary["task_runs"] >= 1
    assert summary["events"] >= 3
    assert history.exists()
    assert history.with_suffix(".db").exists()
