from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from prefect_compat import InMemoryControlPlane, RunState


def test_duplicate_token_race(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "race.jsonl"))
    run = plane.create_flow_run("race")
    token = uuid4()

    def transition() -> str:
        return plane.set_flow_state(
            run.run_id, RunState.PENDING, token, "propose", expected_version=0
        ).status

    with ThreadPoolExecutor(max_workers=8) as ex:
        statuses = list(ex.map(lambda _: transition(), range(32)))

    assert statuses.count("applied") == 1
    assert statuses.count("duplicate") == 31
    assert len(plane.events()) == 1


def test_transition_load_progression(tmp_path):
    plane = InMemoryControlPlane(history_path=str(tmp_path / "load.jsonl"))
    total_runs = 500

    for i in range(total_runs):
        run = plane.create_flow_run(f"flow-{i}")
        plane.set_flow_state(run.run_id, RunState.PENDING, uuid4(), "propose", expected_version=0)
        plane.set_flow_state(run.run_id, RunState.RUNNING, uuid4(), "start", expected_version=1)
        plane.set_flow_state(
            run.run_id, RunState.COMPLETED, uuid4(), "complete", expected_version=2
        )

    assert len(plane.events()) == total_runs * 3
