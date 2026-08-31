from __future__ import annotations

import threading
import time
from pathlib import Path
from uuid import UUID

from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task
from prefect_compat.hooks import on_transition
from prefect_compat.runtime import RunState
from prefect_compat.task_runners import ThreadPoolTaskRunner
from prefect_compat.worker import execute_claimed_deployment_run


def _plane(tmp_path: Path, name: str = "resume") -> InMemoryControlPlane:
    plane = InMemoryControlPlane(history_path=str(tmp_path / f"{name}.jsonl"))
    set_control_plane(plane)
    return plane


def _run_claimed(plane: InMemoryControlPlane, registry: dict) -> dict:
    claimed = plane.claim_next_deployment_run(
        worker_name="resume-worker", lease_seconds=30
    )
    assert claimed is not None
    execute_claimed_deployment_run(plane, claimed, registry)
    finished = plane.get_deployment_run(UUID(str(claimed["id"])))
    assert finished is not None
    assert finished["status"] == "COMPLETED"
    assert finished["flow_run_id"]
    return finished


def test_none_result_auto_skips_on_resume(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "none-auto")
    calls = {"setup": 0, "work": 0}

    @task
    def setup() -> None:
        calls["setup"] += 1

    @task
    def work() -> str:
        calls["work"] += 1
        return "done"

    @flow
    def pipeline() -> str:
        setup.submit()
        return work.submit().result()

    assert pipeline() == "done"
    first = plane.latest_flow()
    assert first is not None
    assert calls == {"setup": 1, "work": 1}

    plane.prepare_resume(first.run_id)
    assert pipeline() == "done"
    assert calls == {"setup": 1, "work": 2}


def test_persist_result_skips_and_restores_value_on_resume(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "persist")
    calls = {"expensive": 0}

    @task(persist_result=True)
    def expensive(x: int) -> dict:
        calls["expensive"] += 1
        return {"x": x, "n": 42}

    @task
    def add_one(payload: dict) -> int:
        return int(payload["n"]) + 1

    @flow
    def pipeline(x: int = 1) -> int:
        got = expensive.submit(x)
        return add_one.submit(got).result()

    assert pipeline(1) == 43
    first = plane.latest_flow()
    assert first is not None
    assert calls["expensive"] == 1

    arts = plane.list_artifacts_for_flow(first.run_id)
    expensive_art = next(a for a in arts if a["key"] == "expensive-result")
    assert '"result"' in (expensive_art.get("summary") or "")
    assert '"x": 1' in (expensive_art.get("summary") or "") or '"x":1' in (
        expensive_art.get("summary") or ""
    )

    plane.prepare_resume(first.run_id)
    assert pipeline(1) == 43
    assert calls["expensive"] == 1

    second = plane.latest_flow()
    assert second is not None
    assert second.run_id != first.run_id
    second_tasks = plane.list_task_runs(second.run_id).items
    expensive_rows = [t for t in second_tasks if t["task_name"] == "expensive"]
    assert len(expensive_rows) == 1
    assert expensive_rows[0]["state"] == "COMPLETED"


def test_non_persist_non_none_recomputes_on_resume(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "recompute")
    calls = {"volatile": 0}

    @task
    def volatile(x: int) -> int:
        calls["volatile"] += 1
        return x + 1

    @flow
    def pipeline(x: int = 1) -> int:
        return volatile.submit(x).result()

    assert pipeline(1) == 2
    first = plane.latest_flow()
    assert first is not None
    assert calls["volatile"] == 1

    plane.prepare_resume(first.run_id)
    assert pipeline(1) == 2
    assert calls["volatile"] == 2


def test_persist_rejects_non_json_and_recomutes_on_resume(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "bad-type")
    calls = {"bad": 0}

    class Box:
        def __init__(self, n: int) -> None:
            self.n = n

    @task(persist_result=True)
    def bad() -> Box:
        calls["bad"] += 1
        return Box(7)

    @flow
    def pipeline() -> int:
        box = bad.submit().result()
        return box.n

    assert pipeline() == 7
    first = plane.latest_flow()
    assert first is not None
    assert calls["bad"] == 1

    plane.prepare_resume(first.run_id)
    assert pipeline() == 7
    assert calls["bad"] == 2


def test_repeated_task_nodes_resume_independently(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "repeat")
    calls: list[str] = []

    @task
    def status(message: str) -> None:
        calls.append(message)

    @task(persist_result=True)
    def work(x: int) -> int:
        calls.append(f"work:{x}")
        return x + 1

    @flow
    def pipeline() -> int:
        status.submit("start")
        mid = work.submit(1)
        status.submit("end")
        return mid.result()

    assert pipeline() == 2
    first = plane.latest_flow()
    assert first is not None
    assert calls == ["start", "work:1", "end"]

    plane.prepare_resume(first.run_id)
    assert pipeline() == 2
    # None status tasks skip; work persists and skips
    assert calls == ["start", "work:1", "end"]


def test_fresh_run_without_prepare_resume_does_not_skip(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "fresh")
    calls = {"setup": 0}

    @task
    def setup() -> None:
        calls["setup"] += 1

    @flow
    def pipeline() -> None:
        setup.submit()

    pipeline()
    pipeline()
    assert calls["setup"] == 2
    assert plane.latest_flow() is not None


def test_changed_flow_parameters_disable_resume_skips(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "params")
    calls = {"expensive": 0}

    @task(persist_result=True)
    def expensive(x: int) -> dict:
        calls["expensive"] += 1
        return {"x": x}

    @flow
    def pipeline(x: int = 1) -> dict:
        return expensive.submit(x).result()

    assert pipeline(1) == {"x": 1}
    first = plane.latest_flow()
    assert first is not None
    plane.prepare_resume(first.run_id)
    assert pipeline(999) == {"x": 999}
    assert calls["expensive"] == 2


def test_upstream_recompute_invalidates_downstream_persist(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "upstream")
    n = {"upstream": 0}

    @task
    def upstream() -> int:
        n["upstream"] += 1
        return n["upstream"]

    @task(persist_result=True)
    def downstream(x: int) -> dict:
        return {"saw": x}

    @flow
    def pipeline() -> dict:
        return downstream.submit(upstream.submit()).result()

    assert pipeline() == {"saw": 1}
    first = plane.latest_flow()
    assert first is not None
    plane.prepare_resume(first.run_id)
    assert pipeline() == {"saw": 2}
    assert n["upstream"] == 2


def test_threadpool_map_skips_on_resume(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "map")
    calls = {"m": 0}

    @task(persist_result=True)
    def work(x: int) -> int:
        calls["m"] += 1
        return x * 2

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=2))
    def pipeline() -> list[int]:
        return [f.result() for f in work.map([1, 2, 3])]

    assert pipeline() == [2, 4, 6]
    first = plane.latest_flow()
    assert first is not None
    assert calls["m"] == 3
    plane.prepare_resume(first.run_id)
    assert pipeline() == [2, 4, 6]
    assert calls["m"] == 3


def test_cache_hit_does_not_refire_transition_hooks(tmp_path: Path) -> None:
    plane = _plane(tmp_path, "hooks")
    hooks = {"completed": 0}

    def on_completed(_ctx: object) -> None:
        hooks["completed"] += 1

    @task(
        persist_result=True,
        transition_hooks=[
            on_transition(
                on_completed, from_state=RunState.RUNNING, to_state=RunState.COMPLETED
            )
        ],
    )
    def expensive(x: int) -> int:
        return x + 1

    @flow
    def pipeline(x: int = 1) -> int:
        return expensive.submit(x).result()

    assert pipeline(1) == 2
    assert hooks["completed"] == 1
    first = plane.latest_flow()
    assert first is not None
    plane.prepare_resume(first.run_id)
    assert pipeline(1) == 2
    assert hooks["completed"] == 1


def test_deployment_cancel_mid_run_then_retry_skips(tmp_path: Path) -> None:
    """Cancel while RUNNING, then retry — eligible completed tasks skip."""
    plane = _plane(tmp_path, "deploy-cancel-retry")
    calls = {"setup": 0, "expensive": 0, "slow": 0}
    entered_slow = threading.Event()

    @task
    def setup() -> None:
        calls["setup"] += 1

    @task(persist_result=True)
    def expensive(x: int) -> dict:
        calls["expensive"] += 1
        return {"x": x, "n": 42}

    @task
    def slow() -> str:
        # Non-None without persist_result → must recompute on resume.
        calls["slow"] += 1
        entered_slow.set()
        time.sleep(2.0)
        return "slept"

    @flow(
        name="resume_cancel_pipeline", task_runner=ThreadPoolTaskRunner(max_workers=2)
    )
    def pipeline(n: int = 1) -> int:
        setup.submit()
        payload = expensive.submit(n)
        slow.submit()
        return int(payload.result()["n"])

    registry = {"resume_cancel_pipeline": pipeline}
    dep = plane.create_deployment(
        name="resume-cancel-dep",
        flow_name="resume_cancel_pipeline",
        default_parameters={"n": 1},
        paused=False,
    )
    first_dep = plane.trigger_deployment_run(UUID(dep["id"]), parameters={"n": 1})
    assert first_dep["status"] == "SCHEDULED"
    claimed = plane.claim_next_deployment_run(
        worker_name="resume-worker", lease_seconds=30
    )
    assert claimed is not None

    def _worker() -> None:
        execute_claimed_deployment_run(plane, claimed, registry)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    assert entered_slow.wait(timeout=5.0)
    # Flow run id is attached when @flow starts; poll briefly.
    flow_run_id: UUID | None = None
    for _ in range(50):
        cur = plane.get_deployment_run(UUID(str(claimed["id"])))
        if cur and cur.get("flow_run_id"):
            flow_run_id = UUID(str(cur["flow_run_id"]))
            break
        time.sleep(0.05)
    assert flow_run_id is not None
    cancelled = plane.cancel_flow_run(flow_run_id)
    assert cancelled["state"] == "CANCELLED"
    t.join(timeout=5.0)
    assert not t.is_alive()
    assert calls["setup"] == 1
    assert calls["expensive"] == 1

    retry_dep = plane.retry_flow_run(flow_run_id)
    assert retry_dep.get("resume_from_flow_run_id") == str(flow_run_id)
    second = _run_claimed(plane, registry)
    second_flow_id = UUID(str(second["flow_run_id"]))
    assert second_flow_id != flow_run_id
    assert calls["setup"] == 1
    assert calls["expensive"] == 1
    assert calls["slow"] >= 2  # cancelled attempt + retry recompute
