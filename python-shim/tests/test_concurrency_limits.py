"""Global + tag concurrency limits (Phases 1–3)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prefect_compat import (
    ConcurrencyLimitError,
    ConcurrencySlotTimeoutError,
    concurrency,
    create_concurrency_limit,
    create_tag_concurrency_limit,
    flow,
    rate_limit,
    set_control_plane,
    task,
)
from prefect_compat.runtime import InMemoryControlPlane, RunState
from prefect_compat.server import app, control_plane
from prefect_compat.task_runners import ThreadPoolTaskRunner


def _plane(tmp_path: Path) -> InMemoryControlPlane:
    return InMemoryControlPlane(history_path=str(tmp_path / "gcl.jsonl"))


def test_create_acquire_release_limit(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    create_concurrency_limit("database", 2, plane=plane)
    lim = plane.get_concurrency_limit("database")
    assert lim is not None
    assert lim["limit"] == 2

    a1 = plane.acquire_concurrency_slots(["database"], occupy=1, lease_duration=60)
    a2 = plane.acquire_concurrency_slots(["database"], occupy=1, lease_duration=60)
    assert a1["status"] == "acquired"
    assert a2["status"] == "acquired"
    blocked = plane.acquire_concurrency_slots(["database"], occupy=1, lease_duration=60)
    assert blocked["status"] == "would_block"
    plane.release_concurrency_slots(a1["lease_ids"])
    a3 = plane.acquire_concurrency_slots(["database"], occupy=1, lease_duration=60)
    assert a3["status"] == "acquired"


def test_concurrency_context_manager_caps_parallelism(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    create_concurrency_limit("db", 2, plane=plane)
    active = 0
    peak = 0
    lock = threading.Lock()

    def work() -> None:
        nonlocal active, peak
        with concurrency("db", plane=plane, poll_seconds=0.02):
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.15)
            with lock:
                active -= 1

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()
    assert peak <= 2


def test_strict_missing_limit_raises(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    with pytest.raises(ConcurrencyLimitError):
        with concurrency("missing-limit", strict=True, plane=plane, timeout_seconds=0.5):
            pass


def test_soft_missing_limit_bypasses(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    with concurrency("missing-soft", plane=plane):
        pass


def test_acquire_timeout(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    create_concurrency_limit("solo", 1, plane=plane)
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with concurrency("solo", plane=plane):
            held.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(timeout=2)
    with pytest.raises(ConcurrencySlotTimeoutError):
        with concurrency("solo", plane=plane, timeout_seconds=0.2, poll_seconds=0.05):
            pass
    release.set()
    t.join(timeout=5)


def test_rate_limit_requires_decay_and_paces(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    create_concurrency_limit("api", 1, plane=plane)
    with pytest.raises(ConcurrencyLimitError):
        rate_limit("api", plane=plane, timeout_seconds=0.5)

    create_concurrency_limit("api", 1, slot_decay_per_second=10.0, plane=plane)
    t0 = time.perf_counter()
    for _ in range(3):
        rate_limit("api", plane=plane, timeout_seconds=5.0, poll_seconds=0.01)
    elapsed = time.perf_counter() - t0
    # 3 acquires with decay 10/s and occupy 1 → ~0.2s between freed slots.
    assert elapsed >= 0.15


def test_tag_limit_caps_threadpool_map(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    create_tag_concurrency_limit("db", 1, plane=plane)
    active = 0
    peak = 0
    lock = threading.Lock()

    @task(tags=["db"])
    def slow(x: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.12)
        with lock:
            active -= 1
        return x

    @flow(task_runner=ThreadPoolTaskRunner(max_workers=4))
    def fan() -> list[int]:
        return [f.result() for f in slow.map([1, 2, 3, 4])]

    assert fan() == [1, 2, 3, 4]
    assert peak == 1


def test_tag_limit_zero_aborts(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    create_tag_concurrency_limit("blocked", 0, plane=plane)

    @task(tags=["blocked"])
    def nope() -> int:
        return 1

    @flow
    def boom() -> int:
        return nope.submit().result()

    with pytest.raises(ConcurrencyLimitError):
        boom()


def test_multi_tag_and_semantics(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    create_tag_concurrency_limit("a", 1, plane=plane)
    create_tag_concurrency_limit("b", 1, plane=plane)

    held = plane.acquire_concurrency_slots(
        ["tag:a"], occupy=1, lease_duration=30, holder_type="test", holder_id="1"
    )
    assert held["status"] == "acquired"

    from prefect_compat.concurrency import acquire_tag_slots_for_task

    with pytest.raises(ConcurrencySlotTimeoutError):
        acquire_tag_slots_for_task(
            ["a", "b"],
            task_run_id="x",
            timeout_seconds=0.25,
            poll_seconds=0.05,
            plane=plane,
        )


def test_http_crud(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    control_plane._flows = plane._flows
    control_plane._tasks = plane._tasks
    control_plane._events = plane._events
    control_plane._tokens = plane._tokens
    control_plane._history_path = plane._history_path
    control_plane._sqlite_path = plane._sqlite_path
    control_plane._sqlite_conn = plane._sqlite_conn
    control_plane._rust_bridge = plane._rust_bridge
    control_plane._rust_fsm_bridge = plane._rust_fsm_bridge
    control_plane._rust_fsm_handle = plane._rust_fsm_handle
    control_plane._rust_native_persistence = plane._rust_native_persistence
    control_plane._rust_db_bound = plane._rust_db_bound
    control_plane._lock = plane._lock
    set_control_plane(control_plane)

    client = TestClient(app)
    r = client.post(
        "/api/concurrency-limits",
        json={"name": "http-db", "limit": 3, "slot_decay_per_second": 1.5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "http-db"
    assert body["limit"] == 3
    g = client.get("/api/concurrency-limits/http-db")
    assert g.status_code == 200
    listed = client.get("/api/concurrency-limits").json()["limits"]
    assert any(x["name"] == "http-db" for x in listed)
    patch = client.patch("/api/concurrency-limits/http-db", json={"limit": 5})
    assert patch.status_code == 200
    assert patch.json()["limit"] == 5
    deleted = client.delete("/api/concurrency-limits/http-db")
    assert deleted.status_code == 200
    assert client.get("/api/concurrency-limits/http-db").status_code == 404


def test_lease_reclaim_frees_slots(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    create_concurrency_limit("ephemeral", 1, plane=plane)
    now0 = "2020-01-01T00:00:00+00:00"
    a = plane.acquire_concurrency_slots(
        ["ephemeral"], occupy=1, lease_duration=1, now=now0
    )
    assert a["status"] == "acquired"
    blocked = plane.acquire_concurrency_slots(
        ["ephemeral"], occupy=1, lease_duration=1, now="2020-01-01T00:00:00.500+00:00"
    )
    assert blocked["status"] == "would_block"
    free = plane.acquire_concurrency_slots(
        ["ephemeral"], occupy=1, lease_duration=1, now="2020-01-01T00:00:02+00:00"
    )
    assert free["status"] == "acquired"


def test_tagged_task_leaves_running_while_holding_slot(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    set_control_plane(plane)
    create_tag_concurrency_limit("chk", 1, plane=plane)
    seen: list[RunState] = []

    @task(tags=["chk"])
    def probe(x: int) -> int:
        # During body, a second acquire should block (would_block).
        blocked = plane.acquire_concurrency_slots(
            ["tag:chk"], occupy=1, lease_duration=5
        )
        seen.append(blocked["status"])  # type: ignore[arg-type]
        return x

    @flow
    def run() -> int:
        return probe.submit(7).result()

    assert run() == 7
    assert seen == ["would_block"]
