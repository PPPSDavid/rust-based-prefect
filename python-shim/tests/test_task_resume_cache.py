from __future__ import annotations

from pathlib import Path

from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task


def _plane(tmp_path: Path, name: str = "resume") -> InMemoryControlPlane:
    plane = InMemoryControlPlane(history_path=str(tmp_path / f"{name}.jsonl"))
    set_control_plane(plane)
    return plane


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
