"""Subflow benchmark workloads for ``perf_matrix.py`` (M1 inline + M2 deployment)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from prefect_compat import InMemoryControlPlane, deployment_ref, flow, set_control_plane, task
from prefect_compat.cancellation import FlowRunCancelled, sleep_cancelable
from prefect_compat.worker import run_worker_loop

CHILD_DEPLOY_NAME = "subflow-perf-child-deploy"
SLOW_DEPLOY_NAME = "subflow-perf-slow-child"
POOL_A = "default-process-pool"
POOL_B = "subflow-perf-pool-b"


def _timed(latencies: dict[str, list[float]], key: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    latencies.setdefault(key, []).append((time.perf_counter() - start) * 1000.0)
    return result


def _start_workers(
    plane: InMemoryControlPlane,
    registry: dict[str, Any],
    *,
    pool_id: str = POOL_A,
    worker_count: int = 1,
    worker_name: str = "subflow-perf-worker",
) -> tuple[threading.Event, list[threading.Thread]]:
    stop = threading.Event()
    threads: list[threading.Thread] = []
    for idx in range(max(1, worker_count)):
        thread = threading.Thread(
            target=run_worker_loop,
            kwargs={
                "control_plane": plane,
                "worker_name": f"{worker_name}-{idx}",
                "work_pool_id": pool_id,
                "flow_registry": registry,
                "lease_seconds": 60,
                "stop_event": stop,
            },
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    return stop, threads


def _ensure_chain_deploy(plane: InMemoryControlPlane, registry: dict[str, Any], *, pool_id: str = POOL_A) -> None:
    if "chain_child" in registry:
        return

    @flow
    def chain_child(k: int = 0) -> int:
        if k <= 0:
            return 1
        return deployment_ref(CHILD_DEPLOY_NAME).submit(k=k - 1).result() + 1

    registry["chain_child"] = chain_child
    existing = plane.get_deployment_by_name(CHILD_DEPLOY_NAME)
    if existing is None:
        plane.create_deployment(
            name=CHILD_DEPLOY_NAME,
            flow_name="chain_child",
            default_parameters={},
            paused=False,
            work_pool_id=pool_id,
        )


def _build_inline_depth_flows(depth: int, fanout: int) -> tuple[Callable[[], int], dict[str, Any]]:
    registry: dict[str, Any] = {}

    @task
    def inc(i: int) -> int:
        return i + 1

    @flow
    def leaf_flow(n: int) -> int:
        total = 0
        for i in range(fanout):
            total += inc.submit(n + i).result()
        return total

    registry["leaf_flow"] = leaf_flow
    child_fn: Callable[[int], int] = leaf_flow
    for level in range(1, max(1, depth)):
        bound_child = child_fn
        level_name = f"inline_level_{level}"

        def _make_parent(inner: Callable[[int], int], name: str) -> Callable[[int], int]:
            @flow(name=name)
            def parent_flow(n: int) -> int:
                return inner(n)

            return parent_flow

        parent_fn = _make_parent(bound_child, level_name)
        registry[level_name] = parent_fn
        child_fn = parent_fn

    @flow
    def root_flow() -> int:
        return child_fn(0)

    registry["root_flow"] = root_flow
    return root_flow, registry


def run_inline_depth(
    plane: InMemoryControlPlane,
    latencies: dict[str, list[float]],
    *,
    depth: int,
    fanout: int,
    iterations: int,
    warmup_iters: int,
) -> dict[str, int]:
    set_control_plane(plane)
    root_flow, _registry = _build_inline_depth_flows(max(1, depth), max(1, fanout))
    for _ in range(warmup_iters):
        root_flow()
    counts = {"inline_depth_runs": 0, "flows_created": 0}
    for _ in range(iterations):
        _timed(latencies, "subflow.inline_depth_ms", root_flow)
        counts["inline_depth_runs"] += 1
        counts["flows_created"] += depth
    return counts


def _close_plane(plane: InMemoryControlPlane) -> None:
    conn = getattr(plane, "_sqlite_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    bridge = getattr(plane, "_rust_fsm_bridge", None)
    handle = int(getattr(plane, "_rust_fsm_handle", 0) or 0)
    if bridge is not None and handle:
        try:
            bridge.engine_free(handle)
        except Exception:
            pass


def _worker_count_for_profile(profile: str, flow_count: int, tasks_per_flow: int) -> int:
    if profile == "deploy_wait_chain":
        return max(3, flow_count)
    if profile == "deploy_cross_pool":
        return max(3, max(2, tasks_per_flow))
    if profile == "cancel_propagation":
        return max(2, flow_count)
    return 1


def _run_isolated_deploy_sample(
    profile: str,
    history_path: str,
    *,
    flow_count: int,
    tasks_per_flow: int,
    sample_fn: Callable[[InMemoryControlPlane, dict[str, Any]], None],
    prepare: Callable[[InMemoryControlPlane], str] | None = None,
) -> None:
    plane = InMemoryControlPlane(history_path=history_path)
    set_control_plane(plane)
    registry: dict[str, Any] = {}
    work_pool_id = prepare(plane) if prepare is not None else POOL_A
    stop, workers = _start_workers(
        plane,
        registry,
        pool_id=work_pool_id,
        worker_count=_worker_count_for_profile(profile, flow_count, tasks_per_flow),
        worker_name=f"subflow-perf-{profile}-{uuid4().hex[:6]}",
    )
    try:
        sample_fn(plane, registry)
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=20)
        _close_plane(plane)


def run_query_dag_nested(
    plane: InMemoryControlPlane,
    latencies: dict[str, list[float]],
    *,
    depth: int,
    query_iterations: int,
    warmup_iters: int,
) -> dict[str, int]:
    set_control_plane(plane)
    root_flow, _registry = _build_inline_depth_flows(max(1, depth), fanout=1)
    root_flow()
    root_run = next(f for f in plane._flows.values() if f.name == "root_flow")

    def _query_tree() -> int:
        detail = plane.get_flow_run_detail(root_run.run_id)
        _timed(latencies, "subflow.detail_query_ms", plane.get_flow_run_detail, root_run.run_id)
        _timed(latencies, "subflow.dag_query_ms", plane.get_flow_run_dag, root_run.run_id, "logical")
        reads = 2
        for child in detail.get("children") or []:
            plane.get_flow_run_dag(UUID(str(child["id"])), mode="logical")
            reads += 1
        return reads

    for _ in range(warmup_iters):
        _query_tree()

    counts = {"dag_query_passes": 0, "read_queries": 0}
    for _ in range(query_iterations):
        reads = _query_tree()
        counts["dag_query_passes"] += 1
        counts["read_queries"] += reads
    return counts


def run_subflow_profile(
    profile: str,
    *,
    flow_count: int,
    tasks_per_flow: int,
    sample_iterations: int,
    plane: InMemoryControlPlane,
    latencies: dict[str, list[float]],
    warmup: bool,
    history_dir: Path | None = None,
) -> dict[str, int]:
    iterations = max(1, 1 if warmup else sample_iterations)
    warmup_iters = 0 if warmup else max(0, min(2, sample_iterations // 3))
    hist_root = history_dir or Path(getattr(plane, "_history_path", "") or ".").parent

    if profile == "inline_depth":
        return run_inline_depth(
            plane,
            latencies,
            depth=flow_count,
            fanout=tasks_per_flow,
            iterations=iterations,
            warmup_iters=warmup_iters,
        )

    if profile == "query_dag_nested":
        return run_query_dag_nested(
            plane,
            latencies,
            depth=flow_count,
            query_iterations=max(1, tasks_per_flow if not warmup else max(1, tasks_per_flow // 5)),
            warmup_iters=warmup_iters,
        )

    if profile == "deploy_wait_chain":
        counts = {"deploy_wait_runs": 0, "flows_created": 0}
        depth = flow_count
        start_k = max(0, depth - 1)

        def _sample(plane: InMemoryControlPlane, registry: dict[str, Any]) -> None:
            set_control_plane(plane)
            _ensure_chain_deploy(plane, registry)

            @flow
            def parent_flow() -> int:
                return deployment_ref(CHILD_DEPLOY_NAME).submit(k=start_k).result()

            registry["parent_flow"] = parent_flow
            parent_flow()

        for idx in range(warmup_iters):
            _run_isolated_deploy_sample(
                profile,
                str(hist_root / f"warmup-{idx}.jsonl"),
                flow_count=flow_count,
                tasks_per_flow=tasks_per_flow,
                sample_fn=_sample,
            )
        for idx in range(iterations):
            def _timed_sample(plane: InMemoryControlPlane, registry: dict[str, Any]) -> None:
                set_control_plane(plane)
                _ensure_chain_deploy(plane, registry)

                @flow
                def parent_flow() -> int:
                    return deployment_ref(CHILD_DEPLOY_NAME).submit(k=start_k).result()

                registry["parent_flow"] = parent_flow
                _timed(latencies, "subflow.deploy_wait_ms", parent_flow)

            _run_isolated_deploy_sample(
                profile,
                str(hist_root / f"sample-{idx}.jsonl"),
                flow_count=flow_count,
                tasks_per_flow=tasks_per_flow,
                sample_fn=_timed_sample,
            )
            counts["deploy_wait_runs"] += 1
            counts["flows_created"] += depth
        return counts

    if profile == "deploy_cross_pool":
        counts = {"deploy_cross_pool_runs": 0}
        depth = max(2, tasks_per_flow)
        start_k = max(0, depth - 1)
        pool_ids: list[str] = []

        def _prepare_cross_pool(plane: InMemoryControlPlane) -> str:
            pool = plane.create_work_pool(POOL_B, pool_type="process")
            pool_id = str(pool["id"])
            pool_ids.clear()
            pool_ids.append(pool_id)
            return pool_id

        def _cross_pool_sample(plane: InMemoryControlPlane, registry: dict[str, Any], *, timed: bool) -> None:
            set_control_plane(plane)
            _ensure_chain_deploy(plane, registry, pool_id=pool_ids[0])

            @flow
            def parent_flow() -> int:
                return deployment_ref(CHILD_DEPLOY_NAME).submit(k=start_k).result()

            registry["parent_flow"] = parent_flow
            if timed:
                _timed(latencies, "subflow.deploy_cross_pool_ms", parent_flow)
            else:
                parent_flow()

        for idx in range(warmup_iters):
            _run_isolated_deploy_sample(
                profile,
                str(hist_root / f"warmup-{idx}.jsonl"),
                flow_count=flow_count,
                tasks_per_flow=tasks_per_flow,
                prepare=_prepare_cross_pool,
                sample_fn=lambda p, r: _cross_pool_sample(p, r, timed=False),
            )
        for idx in range(iterations):
            _run_isolated_deploy_sample(
                profile,
                str(hist_root / f"sample-{idx}.jsonl"),
                flow_count=flow_count,
                tasks_per_flow=tasks_per_flow,
                prepare=_prepare_cross_pool,
                sample_fn=lambda p, r: _cross_pool_sample(p, r, timed=True),
            )
            counts["deploy_cross_pool_runs"] += 1
        return counts

    if profile == "fire_forget_burst":
        counts = {"fire_forget_bursts": 0, "subflow_submits": 0}
        burst = flow_count
        warm_n = max(1, burst // 10)

        def _burst_sample(plane: InMemoryControlPlane, registry: dict[str, Any], *, count: int, timed: bool) -> None:
            set_control_plane(plane)

            @flow
            def noop_child() -> int:
                return 1

            registry["noop_child"] = noop_child
            if plane.get_deployment_by_name(CHILD_DEPLOY_NAME) is None:
                plane.create_deployment(
                    name=CHILD_DEPLOY_NAME,
                    flow_name="noop_child",
                    default_parameters={},
                    paused=False,
                )

            @flow
            def burst_parent(n: int) -> None:
                for i in range(n):
                    deployment_ref(CHILD_DEPLOY_NAME).submit(n=i)

            registry["burst_parent"] = burst_parent
            if timed:
                _timed(latencies, "subflow.fire_forget_burst_ms", burst_parent, count)
            else:
                burst_parent(count)

        for idx in range(warmup_iters):
            _run_isolated_deploy_sample(
                profile,
                str(hist_root / f"warmup-{idx}.jsonl"),
                flow_count=flow_count,
                tasks_per_flow=tasks_per_flow,
                sample_fn=lambda p, r: _burst_sample(p, r, count=warm_n, timed=False),
            )
        for idx in range(iterations):
            _run_isolated_deploy_sample(
                profile,
                str(hist_root / f"sample-{idx}.jsonl"),
                flow_count=flow_count,
                tasks_per_flow=tasks_per_flow,
                sample_fn=lambda p, r: _burst_sample(p, r, count=burst, timed=True),
            )
            counts["fire_forget_bursts"] += 1
            counts["subflow_submits"] += burst
        return counts

    if profile == "cancel_propagation":
        counts = {"cancel_ops": 0}
        child_count = flow_count

        def _cancel_sample(plane: InMemoryControlPlane, registry: dict[str, Any]) -> None:
            set_control_plane(plane)

            @flow
            def slow_child() -> None:
                try:
                    sleep_cancelable(5.0, poll_seconds=0.05)
                except FlowRunCancelled:
                    raise

            @flow
            def parent_launcher() -> UUID:
                from prefect_compat.decorators import _ACTIVE_FLOW_RUN

                for _ in range(child_count):
                    deployment_ref(SLOW_DEPLOY_NAME).submit()
                sleep_cancelable(0.15, poll_seconds=0.05)
                active = _ACTIVE_FLOW_RUN.get()
                assert active is not None
                return active

            registry["slow_child"] = slow_child
            registry["parent_launcher"] = parent_launcher
            if plane.get_deployment_by_name(SLOW_DEPLOY_NAME) is None:
                plane.create_deployment(
                    name=SLOW_DEPLOY_NAME,
                    flow_name="slow_child",
                    default_parameters={},
                    paused=False,
                )

            parent_id_holder: list[UUID] = []
            errors: list[BaseException] = []

            def _run_parent() -> None:
                try:
                    parent_id_holder.append(parent_launcher())
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=_run_parent, daemon=True)
            thread.start()
            thread.join(timeout=8.0)
            if errors:
                raise errors[0]
            if not parent_id_holder:
                raise RuntimeError("parent_launcher did not return a flow run id")
            parent_id = parent_id_holder[0]
            _timed(latencies, "subflow.cancel_propagation_ms", plane.cancel_flow_run, parent_id)
            dep_rows = plane._query_rows(
                "SELECT status FROM deployment_runs WHERE parent_flow_run_id = ?",
                [str(parent_id)],
            )
            if dep_rows and not all(str(r["status"]) == "CANCELLED" for r in dep_rows):
                raise RuntimeError("expected deployment children cancelled")

        for idx in range(iterations):
            _run_isolated_deploy_sample(
                profile,
                str(hist_root / f"sample-{idx}.jsonl"),
                flow_count=flow_count,
                tasks_per_flow=tasks_per_flow,
                sample_fn=_cancel_sample,
            )
            counts["cancel_ops"] += 1
        return counts

    raise ValueError(f"unknown subflow profile: {profile}")
