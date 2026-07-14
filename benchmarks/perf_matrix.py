from __future__ import annotations

import argparse
import ctypes
import concurrent.futures
import json
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PYTHON_SHIM_SRC = ROOT / "python-shim" / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PYTHON_SHIM_SRC) not in sys.path:
    sys.path.insert(0, str(PYTHON_SHIM_SRC))

from prefect_compat.runtime import InMemoryControlPlane, RunState  # noqa: E402

BASE_SEED = 20260416


@dataclass(frozen=True)
class WorkloadRecipe:
    name: str
    flow_count: int
    tasks_per_flow: int
    task_events_per_task: int
    read_ratio: float
    mixed: bool
    cold_start: bool
    sqlite_enabled: bool
    # When set, ``_run_recipe_iteration`` runs the Prefect-compat ``@flow`` / ``@task`` path
    # (including transition hooks) instead of raw control-plane calls. ``flow_count`` is
    # timed iterations per sample; ``tasks_per_flow`` is warmup iterations before the timer.
    decorator_hook_profile: str | None = None
    # When set, run ``@flow`` with ``ThreadPoolTaskRunner`` map workloads (``tasks_per_flow`` = map width).
    decorator_map_width: int | None = None
    # Subflow benchmark profile (inline depth, deploy wait chain, etc.).
    subflow_profile: str | None = None
    # Concurrent reader threads in mixed recipes (writers always use one thread).
    mixed_reader_count: int = 1
    # Exercise FSM batch APIs (task_pending/running/completed) instead of heartbeat events.
    fsm_task_lifecycle: bool = False
    # Global concurrency limit microbench profile (``concurrency`` CM or tagged map).
    gcl_profile: str | None = None


@dataclass
class ProcessSnapshot:
    cpu_seconds: float
    rss_bytes: int


@dataclass
class RecipeRunSample:
    recipe: str
    iteration: int
    warmup: bool
    seed: int
    wall_clock_seconds: float
    counts: dict[str, int]
    throughput: dict[str, float]
    latency_ms: dict[str, dict[str, float]]
    process: dict[str, float]
    sqlite: dict[str, float]


@dataclass
class RecipeAggregate:
    recipe: str
    repetitions: int
    params: dict[str, Any]
    wall_clock_seconds: dict[str, float]
    throughput: dict[str, dict[str, float]]
    latency_ms: dict[str, dict[str, float]]
    process: dict[str, dict[str, float]]
    sqlite: dict[str, dict[str, float]]
    notes: list[str]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(percentile) - 1]


def _latency_stats_ms(values: list[float]) -> dict[str, float]:
    return {
        "count": float(len(values)),
        "p50": _percentile(values, 50),
        "p95": _percentile(values, 95),
        "p99": _percentile(values, 99),
    }


def _cpu_seconds_now() -> float:
    return time.process_time()


def _rss_bytes_now() -> int:
    if sys.platform == "win32":

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        )
        if ok:
            return int(counters.WorkingSetSize)
        return 0
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KB, macOS reports bytes.
        if platform.system().lower() == "darwin":
            return int(usage)
        return int(usage * 1024)
    except Exception:
        return 0


def _process_snapshot() -> ProcessSnapshot:
    return ProcessSnapshot(cpu_seconds=_cpu_seconds_now(), rss_bytes=_rss_bytes_now())


def _git_sha() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def _recipe_catalog() -> dict[str, WorkloadRecipe]:
    return {
        "small_narrow_few_write_cold": WorkloadRecipe(
            name="small_narrow_few_write_cold",
            flow_count=10,
            tasks_per_flow=4,
            task_events_per_task=1,
            read_ratio=0.10,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
        ),
        "medium_wide_heavy_write_warm": WorkloadRecipe(
            name="medium_wide_heavy_write_warm",
            flow_count=50,
            tasks_per_flow=20,
            task_events_per_task=4,
            read_ratio=0.15,
            mixed=False,
            cold_start=False,
            sqlite_enabled=True,
        ),
        "large_wide_heavy_read_warm": WorkloadRecipe(
            name="large_wide_heavy_read_warm",
            flow_count=120,
            tasks_per_flow=24,
            task_events_per_task=3,
            read_ratio=0.80,
            mixed=False,
            cold_start=False,
            sqlite_enabled=True,
        ),
        "medium_narrow_heavy_mixed_warm": WorkloadRecipe(
            name="medium_narrow_heavy_mixed_warm",
            flow_count=40,
            tasks_per_flow=8,
            task_events_per_task=5,
            read_ratio=0.50,
            mixed=True,
            cold_start=False,
            sqlite_enabled=True,
        ),
        "medium_narrow_heavy_mixed_2readers_warm": WorkloadRecipe(
            name="medium_narrow_heavy_mixed_2readers_warm",
            flow_count=40,
            tasks_per_flow=8,
            task_events_per_task=5,
            read_ratio=0.50,
            mixed=True,
            cold_start=False,
            sqlite_enabled=True,
            mixed_reader_count=2,
        ),
        "medium_narrow_fsm_batch_warm": WorkloadRecipe(
            name="medium_narrow_fsm_batch_warm",
            flow_count=40,
            tasks_per_flow=8,
            task_events_per_task=3,
            read_ratio=0.20,
            mixed=False,
            cold_start=False,
            sqlite_enabled=True,
            fsm_task_lifecycle=True,
        ),
        "small_wide_few_mixed_cold": WorkloadRecipe(
            name="small_wide_few_mixed_cold",
            flow_count=15,
            tasks_per_flow=16,
            task_events_per_task=1,
            read_ratio=0.50,
            mixed=True,
            cold_start=True,
            sqlite_enabled=True,
        ),
        # Decorator-path microbench: baseline vs light no-op transition hooks on flow and/or task.
        "micro_decorator_hooks_none": WorkloadRecipe(
            name="micro_decorator_hooks_none",
            flow_count=40,
            tasks_per_flow=10,
            task_events_per_task=0,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            decorator_hook_profile="none",
        ),
        "micro_decorator_hooks_flow_noop": WorkloadRecipe(
            name="micro_decorator_hooks_flow_noop",
            flow_count=40,
            tasks_per_flow=10,
            task_events_per_task=0,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            decorator_hook_profile="flow",
        ),
        "micro_decorator_hooks_task_noop": WorkloadRecipe(
            name="micro_decorator_hooks_task_noop",
            flow_count=40,
            tasks_per_flow=10,
            task_events_per_task=0,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            decorator_hook_profile="task",
        ),
        "micro_decorator_hooks_both_noop": WorkloadRecipe(
            name="micro_decorator_hooks_both_noop",
            flow_count=40,
            tasks_per_flow=10,
            task_events_per_task=0,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            decorator_hook_profile="both",
        ),
        "micro_map_threadpool_narrow": WorkloadRecipe(
            name="micro_map_threadpool_narrow",
            flow_count=20,
            tasks_per_flow=2,
            task_events_per_task=0,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            decorator_map_width=10,
        ),
        "micro_map_threadpool_wide": WorkloadRecipe(
            name="micro_map_threadpool_wide",
            flow_count=10,
            tasks_per_flow=1,
            task_events_per_task=0,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            decorator_map_width=100,
        ),
        "gcl_cm_contention": WorkloadRecipe(
            name="gcl_cm_contention",
            flow_count=20,
            tasks_per_flow=8,
            task_events_per_task=0,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            gcl_profile="cm_contention",
        ),
        "gcl_tag_map_cap": WorkloadRecipe(
            name="gcl_tag_map_cap",
            flow_count=10,
            tasks_per_flow=16,
            task_events_per_task=0,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            gcl_profile="tag_map",
        ),
        "gcl_acquire_micro": WorkloadRecipe(
            name="gcl_acquire_micro",
            flow_count=200,
            tasks_per_flow=1,
            task_events_per_task=0,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            gcl_profile="acquire_micro",
        ),
        "subflow_inline_depth_3": WorkloadRecipe(
            name="subflow_inline_depth_3",
            flow_count=3,
            tasks_per_flow=10,
            task_events_per_task=3,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            subflow_profile="inline_depth",
        ),
        "subflow_deploy_wait_chain": WorkloadRecipe(
            name="subflow_deploy_wait_chain",
            flow_count=3,
            tasks_per_flow=1,
            task_events_per_task=3,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            subflow_profile="deploy_wait_chain",
        ),
        "subflow_deploy_cross_pool": WorkloadRecipe(
            name="subflow_deploy_cross_pool",
            flow_count=1,
            tasks_per_flow=3,
            task_events_per_task=2,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            subflow_profile="deploy_cross_pool",
        ),
        "subflow_fire_forget_burst": WorkloadRecipe(
            name="subflow_fire_forget_burst",
            flow_count=50,
            tasks_per_flow=1,
            task_events_per_task=3,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            subflow_profile="fire_forget_burst",
        ),
        "subflow_cancel_propagation": WorkloadRecipe(
            name="subflow_cancel_propagation",
            flow_count=5,
            tasks_per_flow=1,
            task_events_per_task=2,
            read_ratio=0.0,
            mixed=False,
            cold_start=True,
            sqlite_enabled=True,
            subflow_profile="cancel_propagation",
        ),
        "subflow_query_dag_nested": WorkloadRecipe(
            name="subflow_query_dag_nested",
            flow_count=3,
            tasks_per_flow=25,
            task_events_per_task=1,
            read_ratio=0.0,
            mixed=False,
            cold_start=False,
            sqlite_enabled=True,
            subflow_profile="query_dag_nested",
        ),
    }


def _presets() -> dict[str, list[str]]:
    return {
        "lite": [
            "small_narrow_few_write_cold",
            "medium_narrow_heavy_mixed_warm",
        ],
        "pr": [
            "small_narrow_few_write_cold",
            "medium_wide_heavy_write_warm",
            "medium_narrow_heavy_mixed_warm",
        ],
        "hook_micro": [
            "micro_decorator_hooks_none",
            "micro_decorator_hooks_flow_noop",
            "micro_decorator_hooks_task_noop",
            "micro_decorator_hooks_both_noop",
        ],
        "flow_map": [
            "micro_map_threadpool_narrow",
            "micro_map_threadpool_wide",
        ],
        "concurrency": [
            "medium_narrow_fsm_batch_warm",
            "medium_narrow_heavy_mixed_2readers_warm",
        ],
        "gcl": [
            "gcl_acquire_micro",
            "gcl_cm_contention",
            "gcl_tag_map_cap",
        ],
        "subflow_lite": [
            "subflow_inline_depth_3",
            "subflow_deploy_wait_chain",
            "subflow_query_dag_nested",
        ],
        "subflow": [
            "subflow_inline_depth_3",
            "subflow_deploy_wait_chain",
            "subflow_deploy_cross_pool",
            "subflow_fire_forget_burst",
            "subflow_cancel_propagation",
            "subflow_query_dag_nested",
        ],
        "full": [
            k
            for k in _recipe_catalog().keys()
            if not k.startswith("micro_decorator_hooks_")
            and not k.startswith("micro_map_")
            and not k.startswith("subflow_")
            and not k.startswith("gcl_")
        ],
    }


def parse_recipe_list(raw: str) -> list[str]:
    return [value.strip() for value in raw.split(",") if value.strip()]


def parse_thresholds(raw: str) -> dict[str, float]:
    parsed: dict[str, float] = {}
    if not raw.strip():
        return parsed
    for token in raw.split(","):
        key, value = token.split("=", 1)
        parsed[key.strip()] = float(value.strip())
    return parsed


def canonical_matrix_compare_key(recipes: list[str]) -> str:
    """Stable identity for a benchmark *mode*: same key iff the same preset or the same recipe set.

    Presets (lite, pr, full, hook_micro, …) are recognized when the recipe list matches that preset
    exactly. Otherwise we key by sorted recipe names so arbitrary ``--recipes`` runs compare only to
    runs with the identical set.
    """
    if not recipes:
        return "unknown"
    sorted_unique = sorted(set(recipes))
    for pname, plist in _presets().items():
        if sorted_unique == sorted(set(plist)):
            return f"preset:{pname}"
    return "recipes:" + ",".join(sorted_unique)


def extract_matrix_compare_key(payload: dict[str, Any]) -> str:
    """Read compare key from ``metadata`` or infer from ``recipes`` / ``aggregates`` (legacy runs)."""
    meta = payload.get("metadata")
    if isinstance(meta, dict):
        key = meta.get("matrix_compare_key")
        if isinstance(key, str) and key.strip():
            return key.strip()
    recipes = payload.get("recipes")
    if isinstance(recipes, list) and recipes:
        return canonical_matrix_compare_key([str(x) for x in recipes])
    names: list[str] = []
    for row in payload.get("aggregates", []):
        if isinstance(row, dict) and row.get("recipe") is not None:
            names.append(str(row["recipe"]))
    if not names:
        return "unknown"
    return canonical_matrix_compare_key(names)


def describe_matrix_compare_key(compare_key: str) -> str:
    if compare_key == "unknown":
        return "unknown (missing recipes; re-run with `perf_matrix.py run`)"
    if compare_key.startswith("preset:"):
        return f"preset `{compare_key.split(':', 1)[1]}`"
    if compare_key.startswith("recipes:"):
        body = compare_key.split(":", 1)[1]
        n = len(body.split(",")) if body else 0
        return f"custom recipe set ({n} recipe(s))"
    return compare_key


def load_matrix_run_json(path: Path) -> dict[str, Any]:
    """Load JSON from ``perf_matrix.py run``. Rejects array-shaped A/B reports with a clear error."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        raise ValueError(
            f"{path}: expected a JSON object from `perf_matrix.py run`, but found a JSON array. "
            "Files such as `docs/perf_comparison.json` (Prefect vs IronFlow A/B) are not valid "
            "inputs for `perf_matrix.py compare`; pass two outputs from `perf_matrix.py run`."
        )
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected JSON object, got {type(raw).__name__}")
    return raw


def _timed_call(
    latencies: dict[str, list[float]],
    key: str,
    fn: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    duration_ms = (time.perf_counter() - start) * 1000.0
    latencies.setdefault(key, []).append(duration_ms)
    return result


def _noop_transition_hook(_ctx: Any) -> None:
    """Light user hook body for perf-matrix decorator microbench."""
    return None


def _close_plane_footprint(plane: InMemoryControlPlane) -> None:
    """Close SQLite + native engine resources (Windows-safe tempdir teardown)."""
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
        try:
            setattr(plane, "_rust_fsm_handle", 0)
        except Exception:
            pass


def _run_decorator_hook_micro_iteration(
    recipe: WorkloadRecipe,
    seed: int,
    warmup: bool,
) -> RecipeRunSample:
    """Benchmark ``@flow`` / ``@task`` with optional no-op transition hooks (real shim path)."""
    rng = random.Random(seed)
    _ = rng
    iterations = max(1, int(recipe.flow_count))
    warmup_iters = max(0, int(recipe.tasks_per_flow))
    if warmup:
        iterations = max(3, iterations // 6)
        warmup_iters = max(1, warmup_iters // 3)

    profile = recipe.decorator_hook_profile
    if profile is None:
        raise RuntimeError("decorator hook microbench requires decorator_hook_profile")
    if profile not in {"none", "flow", "task", "both"}:
        raise ValueError(f"Unknown decorator_hook_profile: {profile!r}")

    latencies: dict[str, list[float]] = {}
    notes: list[str] = [f"decorator_hook_micro profile={profile}"]

    with tempfile.TemporaryDirectory(prefix="perf-matrix-hooks-") as td:
        history_path = Path(td) / "history.jsonl"
        db_path = history_path.with_suffix(".db")
        if not recipe.sqlite_enabled:
            history_path = None
            db_path = Path(td) / "unused.db"

        plane = InMemoryControlPlane(
            history_path=str(history_path) if history_path else None
        )
        sqlite_before = float(db_path.stat().st_size) if db_path.exists() else 0.0
        wal_path = db_path.with_suffix(".db-wal")
        wal_before = float(wal_path.stat().st_size) if wal_path.exists() else 0.0
        before_proc = _process_snapshot()

        from prefect_compat import flow, on_transition, set_control_plane, task

        from benchmarks._task_cast import as_task_wrapper

        set_control_plane(plane)
        noop = on_transition(_noop_transition_hook)

        if profile == "none":

            @task
            def _work(x: int) -> int:
                return x + 1

            work = as_task_wrapper(_work)

            @flow
            def sample() -> int:
                return work.submit(1).result()

        elif profile == "flow":

            @task
            def _work(x: int) -> int:
                return x + 1

            work = as_task_wrapper(_work)

            @flow(transition_hooks=[noop])
            def sample() -> int:
                return work.submit(1).result()

        elif profile == "task":

            @task(transition_hooks=[noop])
            def _work(x: int) -> int:
                return x + 1

            work = as_task_wrapper(_work)

            @flow
            def sample() -> int:
                return work.submit(1).result()

        else:

            @task(transition_hooks=[noop])
            def _work(x: int) -> int:
                return x + 1

            work = as_task_wrapper(_work)

            @flow(transition_hooks=[noop])
            def sample() -> int:
                return work.submit(1).result()

        for _ in range(warmup_iters):
            _timed_call(latencies, "decorator_hook_micro.warmup_invocation", sample)

        wall_start = time.perf_counter()
        per_ms: list[float] = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            sample()
            per_ms.append((time.perf_counter() - t0) * 1000.0)
        wall_seconds = time.perf_counter() - wall_start

        after_proc = _process_snapshot()
        sqlite_after = (
            float(db_path.stat().st_size) if db_path.exists() else sqlite_before
        )
        wal_after = float(wal_path.stat().st_size) if wal_path.exists() else wal_before

        events = len(plane.events())
        _close_plane_footprint(plane)

    counts = {
        "flows_created": iterations,
        "tasks_created": iterations,
        "flow_transitions": 0,
        "task_events_recorded": 0,
        "read_queries": 0,
    }
    throughput = {
        "flows_per_sec": iterations / wall_seconds if wall_seconds else 0.0,
        "tasks_per_sec": iterations / wall_seconds if wall_seconds else 0.0,
        "transitions_per_sec": events / wall_seconds if wall_seconds else 0.0,
        "task_events_per_sec": events / wall_seconds if wall_seconds else 0.0,
    }
    latency_ms = {"decorator_hook_micro.invocation_ms": _latency_stats_ms(per_ms)}
    process = {
        "cpu_seconds_used": max(0.0, after_proc.cpu_seconds - before_proc.cpu_seconds),
        "rss_bytes_start": float(before_proc.rss_bytes),
        "rss_bytes_end": float(after_proc.rss_bytes),
        "rss_bytes_delta": float(after_proc.rss_bytes - before_proc.rss_bytes),
    }
    sqlite: dict[str, float] = {
        "db_bytes_before": sqlite_before,
        "db_bytes_after": sqlite_after,
        "db_bytes_growth": max(0.0, sqlite_after - sqlite_before),
        "wal_bytes_before": wal_before,
        "wal_bytes_after": wal_after,
        "wal_bytes_growth": max(0.0, wal_after - wal_before),
        "bytes_per_write_op": 0.0,
    }
    if notes:
        sqlite["notes"] = float(len(notes))

    return RecipeRunSample(
        recipe=recipe.name,
        iteration=0,
        warmup=warmup,
        seed=seed,
        wall_clock_seconds=wall_seconds,
        counts=counts,
        throughput=throughput,
        latency_ms=latency_ms,
        process=process,
        sqlite=sqlite,
    )


def _run_decorator_map_micro_iteration(
    recipe: WorkloadRecipe,
    seed: int,
    warmup: bool,
) -> RecipeRunSample:
    """Benchmark ``@flow`` with ``ThreadPoolTaskRunner`` map (flow plane hot path)."""
    rng = random.Random(seed)
    _ = rng
    iterations = max(1, int(recipe.flow_count))
    warmup_iters = max(0, int(recipe.tasks_per_flow))
    map_width = max(1, int(recipe.decorator_map_width or 1))
    if warmup:
        iterations = max(2, iterations // 4)
        warmup_iters = max(1, warmup_iters // 2)

    latencies: dict[str, list[float]] = {}
    notes: list[str] = [f"decorator_map_micro width={map_width}"]

    with tempfile.TemporaryDirectory(prefix="perf-matrix-map-") as td:
        history_path = Path(td) / "history.jsonl"
        db_path = history_path.with_suffix(".db")
        if not recipe.sqlite_enabled:
            history_path = None
            db_path = Path(td) / "unused.db"

        plane = InMemoryControlPlane(
            history_path=str(history_path) if history_path else None
        )
        sqlite_before = float(db_path.stat().st_size) if db_path.exists() else 0.0
        wal_path = db_path.with_suffix(".db-wal")
        wal_before = float(wal_path.stat().st_size) if wal_path.exists() else 0.0
        before_proc = _process_snapshot()

        from prefect_compat import flow, set_control_plane, task, wait
        from prefect_compat.task_runners import ThreadPoolTaskRunner

        from benchmarks._task_cast import as_task_wrapper

        set_control_plane(plane)

        @task
        def _inc(x: int) -> int:
            return x + 1

        @task
        def _dbl(x: int) -> int:
            return x * 2

        inc = as_task_wrapper(_inc)
        dbl = as_task_wrapper(_dbl)

        @flow(task_runner=ThreadPoolTaskRunner())
        def sample() -> int:
            first = inc.submit(map_width)
            mapped_futs = dbl.map(range(map_width), wait_for=[first])
            wait(mapped_futs)
            return sum(f.result() for f in mapped_futs)

        for _ in range(warmup_iters):
            _timed_call(latencies, "decorator_map_micro.warmup_invocation", sample)

        wall_start = time.perf_counter()
        per_ms: list[float] = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            sample()
            per_ms.append((time.perf_counter() - t0) * 1000.0)
        wall_seconds = time.perf_counter() - wall_start

        after_proc = _process_snapshot()
        sqlite_after = (
            float(db_path.stat().st_size) if db_path.exists() else sqlite_before
        )
        wal_after = float(wal_path.stat().st_size) if wal_path.exists() else wal_before

        tasks_per_flow = map_width + 1
        counts = {
            "flows_created": iterations,
            "tasks_created": iterations * tasks_per_flow,
            "flow_transitions": 0,
            "task_events_recorded": 0,
            "read_queries": 0,
        }
        throughput = {
            "flows_per_sec": iterations / wall_seconds if wall_seconds else 0.0,
            "tasks_per_sec": (iterations * tasks_per_flow) / wall_seconds
            if wall_seconds
            else 0.0,
            "transitions_per_sec": 0.0,
            "task_events_per_sec": 0.0,
        }
        latency_ms = {"decorator_map_micro.invocation_ms": _latency_stats_ms(per_ms)}
        process = {
            "cpu_seconds_used": max(
                0.0, after_proc.cpu_seconds - before_proc.cpu_seconds
            ),
            "rss_bytes_start": float(before_proc.rss_bytes),
            "rss_bytes_end": float(after_proc.rss_bytes),
            "rss_bytes_delta": float(after_proc.rss_bytes - before_proc.rss_bytes),
        }
        sqlite: dict[str, float] = {
            "db_bytes_before": sqlite_before,
            "db_bytes_after": sqlite_after,
            "db_bytes_growth": max(0.0, sqlite_after - sqlite_before),
            "wal_bytes_before": wal_before,
            "wal_bytes_after": wal_after,
            "wal_bytes_growth": max(0.0, wal_after - wal_before),
            "bytes_per_write_op": 0.0,
        }
        if notes:
            sqlite["notes"] = float(len(notes))

        events = len(plane.events())
        _ = events
        _close_plane_footprint(plane)

    return RecipeRunSample(
        recipe=recipe.name,
        iteration=0,
        warmup=warmup,
        seed=seed,
        wall_clock_seconds=wall_seconds,
        counts=counts,
        throughput=throughput,
        latency_ms=latency_ms,
        process=process,
        sqlite=sqlite,
    )


def _run_gcl_iteration(
    recipe: WorkloadRecipe,
    seed: int,
    warmup: bool,
) -> RecipeRunSample:
    """Benchmark global / tag concurrency-limit hot paths."""
    rng = random.Random(seed)
    _ = rng
    profile = recipe.gcl_profile
    if profile is None:
        raise RuntimeError("gcl microbench requires gcl_profile")
    if profile not in {"acquire_micro", "cm_contention", "tag_map"}:
        raise ValueError(f"Unknown gcl_profile: {profile!r}")

    iterations = max(1, int(recipe.flow_count))
    workers = max(1, int(recipe.tasks_per_flow))
    if warmup:
        iterations = max(2, iterations // 4)
        workers = max(1, workers // 2)

    notes: list[str] = [f"gcl_profile={profile}"]
    with tempfile.TemporaryDirectory(prefix="perf-matrix-gcl-") as td:
        history_path = Path(td) / "history.jsonl"
        db_path = history_path.with_suffix(".db")
        plane = InMemoryControlPlane(history_path=str(history_path))
        sqlite_before = float(db_path.stat().st_size) if db_path.exists() else 0.0
        wal_path = db_path.with_suffix(".db-wal")
        wal_before = float(wal_path.stat().st_size) if wal_path.exists() else 0.0
        before_proc = _process_snapshot()

        from prefect_compat import (
            concurrency,
            create_concurrency_limit,
            create_tag_concurrency_limit,
            flow,
            set_control_plane,
            task,
        )
        from prefect_compat.task_runners import ThreadPoolTaskRunner

        from benchmarks._task_cast import as_task_wrapper

        set_control_plane(plane)
        per_ms: list[float] = []

        if profile == "acquire_micro":
            create_concurrency_limit("bench", limit=max(8, workers), plane=plane)
            # Warm schema + FFI path.
            warm = plane.acquire_concurrency_slots(
                ["bench"], occupy=1, lease_duration=60
            )
            if warm.get("status") == "acquired":
                plane.release_concurrency_slots(warm.get("lease_ids") or [])
            wall_start = time.perf_counter()
            for _ in range(iterations):
                t0 = time.perf_counter()
                out = plane.acquire_concurrency_slots(
                    ["bench"], occupy=1, lease_duration=30
                )
                if out.get("status") == "acquired":
                    plane.release_concurrency_slots(out.get("lease_ids") or [])
                per_ms.append((time.perf_counter() - t0) * 1000.0)
            wall_seconds = time.perf_counter() - wall_start
            flows_created = 0
            tasks_created = iterations
            latency_key = "gcl.acquire_release_ms"
        elif profile == "cm_contention":
            create_concurrency_limit("bench", limit=2, plane=plane)

            def _hold() -> None:
                with concurrency("bench", plane=plane, poll_seconds=0.01):
                    time.sleep(0.001)

            wall_start = time.perf_counter()
            for _ in range(iterations):
                t0 = time.perf_counter()
                threads = [
                    threading.Thread(target=_hold) for _ in range(min(8, workers))
                ]
                for th in threads:
                    th.start()
                for th in threads:
                    th.join()
                per_ms.append((time.perf_counter() - t0) * 1000.0)
            wall_seconds = time.perf_counter() - wall_start
            flows_created = 0
            tasks_created = iterations * min(8, workers)
            latency_key = "gcl.cm_contention_ms"
        else:
            create_tag_concurrency_limit("bench", limit=2, plane=plane)

            @task(tags=["bench"])
            def _work(x: int) -> int:
                return x + 1

            work = as_task_wrapper(_work)

            @flow(task_runner=ThreadPoolTaskRunner(max_workers=min(8, workers)))
            def sample() -> list[int]:
                return [f.result() for f in work.map(list(range(workers)))]

            for _ in range(2):
                sample()
            wall_start = time.perf_counter()
            for _ in range(iterations):
                t0 = time.perf_counter()
                sample()
                per_ms.append((time.perf_counter() - t0) * 1000.0)
            wall_seconds = time.perf_counter() - wall_start
            flows_created = iterations
            tasks_created = iterations * workers
            latency_key = "gcl.tag_map_ms"

        after_proc = _process_snapshot()
        sqlite_after = float(db_path.stat().st_size) if db_path.exists() else sqlite_before
        wal_after = float(wal_path.stat().st_size) if wal_path.exists() else wal_before
        _close_plane_footprint(plane)

    counts = {
        "flows_created": flows_created,
        "tasks_created": tasks_created,
        "flow_transitions": 0,
        "task_events_recorded": 0,
        "read_queries": 0,
    }
    throughput = {
        "flows_per_sec": flows_created / wall_seconds if wall_seconds else 0.0,
        "tasks_per_sec": tasks_created / wall_seconds if wall_seconds else 0.0,
        "transitions_per_sec": 0.0,
        "task_events_per_sec": 0.0,
    }
    latency_ms = {latency_key: _latency_stats_ms(per_ms)}
    process = {
        "cpu_seconds_used": max(0.0, after_proc.cpu_seconds - before_proc.cpu_seconds),
        "rss_bytes_start": float(before_proc.rss_bytes),
        "rss_bytes_end": float(after_proc.rss_bytes),
        "rss_bytes_delta": float(after_proc.rss_bytes - before_proc.rss_bytes),
    }
    sqlite = {
        "db_bytes_before": sqlite_before,
        "db_bytes_after": sqlite_after,
        "db_bytes_growth": max(0.0, sqlite_after - sqlite_before),
        "wal_bytes_before": wal_before,
        "wal_bytes_after": wal_after,
        "wal_bytes_growth": max(0.0, wal_after - wal_before),
        "bytes_per_write_op": 0.0,
        "notes": float(len(notes)),
    }
    return RecipeRunSample(
        recipe=recipe.name,
        iteration=0,
        warmup=warmup,
        seed=seed,
        wall_clock_seconds=wall_seconds,
        counts=counts,
        throughput=throughput,
        latency_ms=latency_ms,
        process=process,
        sqlite=sqlite,
    )


def _run_subflow_iteration(
    recipe: WorkloadRecipe,
    seed: int,
    warmup: bool,
) -> RecipeRunSample:
    """Benchmark subflow workloads (inline depth, deployment chains, cancel, DAG reads)."""
    _ = seed
    profile = recipe.subflow_profile
    if profile is None:
        raise RuntimeError("subflow bench requires subflow_profile")

    latencies: dict[str, list[float]] = {}
    notes: list[str] = [f"subflow profile={profile}"]
    isolated_deploy = profile in {
        "deploy_wait_chain",
        "deploy_cross_pool",
        "fire_forget_burst",
        "cancel_propagation",
    }

    with tempfile.TemporaryDirectory(prefix="perf-matrix-subflow-") as td:
        history_path = Path(td) / "history.jsonl"
        db_path = history_path.with_suffix(".db")
        if not recipe.sqlite_enabled or isolated_deploy:
            history_path = None
            db_path = Path(td) / "unused.db"

        plane = InMemoryControlPlane(
            history_path=str(history_path) if history_path else None
        )
        sqlite_before = float(db_path.stat().st_size) if db_path.exists() else 0.0
        wal_path = db_path.with_suffix(".db-wal")
        wal_before = float(wal_path.stat().st_size) if wal_path.exists() else 0.0
        before_proc = _process_snapshot()

        from benchmarks.subflow_perf import run_subflow_profile

        wall_start = time.perf_counter()
        profile_counts = run_subflow_profile(
            profile,
            flow_count=recipe.flow_count,
            tasks_per_flow=recipe.tasks_per_flow,
            sample_iterations=max(1, recipe.task_events_per_task),
            plane=plane,
            latencies=latencies,
            warmup=warmup,
            history_dir=Path(td),
        )
        wall_seconds = time.perf_counter() - wall_start

        after_proc = _process_snapshot()
        sqlite_after = (
            float(db_path.stat().st_size) if db_path.exists() else sqlite_before
        )
        wal_after = float(wal_path.stat().st_size) if wal_path.exists() else wal_before
        events = len(plane.events())
        _close_plane_footprint(plane)
        from prefect_compat import set_control_plane

        set_control_plane(
            InMemoryControlPlane(history_path=str(Path(td) / "teardown.jsonl"))
        )

    counts = {
        "flows_created": int(profile_counts.get("flows_created", 0)),
        "tasks_created": 0,
        "flow_transitions": 0,
        "task_events_recorded": events,
        "read_queries": int(profile_counts.get("read_queries", 0)),
    }
    for key, value in profile_counts.items():
        if key not in counts:
            counts[key] = int(value)

    throughput = {
        "flows_per_sec": counts["flows_created"] / wall_seconds
        if wall_seconds
        else 0.0,
        "tasks_per_sec": 0.0,
        "transitions_per_sec": events / wall_seconds if wall_seconds else 0.0,
        "task_events_per_sec": events / wall_seconds if wall_seconds else 0.0,
    }
    latency_ms = {
        key: _latency_stats_ms(values) for key, values in latencies.items() if values
    }
    process = {
        "cpu_seconds_used": max(0.0, after_proc.cpu_seconds - before_proc.cpu_seconds),
        "rss_bytes_start": float(before_proc.rss_bytes),
        "rss_bytes_end": float(after_proc.rss_bytes),
        "rss_bytes_delta": float(after_proc.rss_bytes - before_proc.rss_bytes),
    }
    sqlite: dict[str, float] = {
        "db_bytes_before": sqlite_before,
        "db_bytes_after": sqlite_after,
        "db_bytes_growth": max(0.0, sqlite_after - sqlite_before),
        "wal_bytes_before": wal_before,
        "wal_bytes_after": wal_after,
        "wal_bytes_growth": max(0.0, wal_after - wal_before),
        "bytes_per_write_op": 0.0,
    }
    if notes:
        sqlite["notes"] = float(len(notes))

    return RecipeRunSample(
        recipe=recipe.name,
        iteration=0,
        warmup=warmup,
        seed=seed,
        wall_clock_seconds=wall_seconds,
        counts=counts,
        throughput=throughput,
        latency_ms=latency_ms,
        process=process,
        sqlite=sqlite,
    )


def _measure_read_queries(
    plane: InMemoryControlPlane,
    flow_ids: list[Any],
    rng: random.Random,
    latencies: dict[str, list[float]],
    reads: int,
) -> None:
    for _ in range(reads):
        choice = rng.random()
        # API shapes: list_flow_runs(state, limit, cursor); list_task_runs / list_events need a real flow_run_id.
        if choice < 0.34:
            _timed_call(
                latencies, "query.list_flow_runs", plane.list_flow_runs, None, 200, None
            )
        elif not flow_ids:
            _timed_call(
                latencies, "query.list_flow_runs", plane.list_flow_runs, None, 200, None
            )
        else:
            rid = flow_ids[rng.randrange(len(flow_ids))]
            if choice < 0.67:
                _timed_call(
                    latencies,
                    "query.list_task_runs",
                    plane.list_task_runs,
                    rid,
                    200,
                    None,
                )
            else:
                _timed_call(
                    latencies, "query.list_events", plane.list_events, rid, 200, None
                )
        if flow_ids and rng.random() < 0.4:
            rid = flow_ids[rng.randrange(len(flow_ids))]
            _timed_call(
                latencies, "query.get_flow_run_detail", plane.get_flow_run_detail, rid
            )


_FSM_TASK_LIFECYCLE = (
    ("task_pending", None),
    ("task_running", None),
    ("task_completed", None),
)


def _record_task_events_for_recipe(
    plane: InMemoryControlPlane,
    latencies: dict[str, list[float]],
    task_run_id: Any,
    recipe: WorkloadRecipe,
    evt_idx: int,
) -> int:
    """Record task events; returns number of events recorded (for throughput counts)."""
    if recipe.fsm_task_lifecycle:
        _timed_call(
            latencies,
            "record_task_event",
            plane.record_task_events_batch,
            task_run_id,
            list(_FSM_TASK_LIFECYCLE),
        )
        return 3
    _timed_call(
        latencies,
        "record_task_event",
        plane.record_task_event,
        task_run_id,
        "heartbeat",
        {"ordinal": evt_idx},
    )
    return 1


def _apply_flow_start_transitions(
    plane: InMemoryControlPlane,
    latencies: dict[str, list[float]],
    flow_run_id: Any,
) -> int:
    """Apply pending → running → completed; returns transition count (3)."""
    transitions = [
        (RunState.PENDING, uuid4(), "bench", 0),
        (RunState.RUNNING, uuid4(), "bench", 1),
        (RunState.COMPLETED, uuid4(), "bench", 2),
    ]
    _timed_call(
        latencies,
        "set_flow_state",
        plane.set_flow_states_batch,
        flow_run_id,
        transitions,
    )
    return 3


def _run_mixed_concurrent(
    plane: InMemoryControlPlane,
    flow_ids: list[Any],
    rng: random.Random,
    latencies: dict[str, list[float]],
    counts: dict[str, int],
    recipe: WorkloadRecipe,
    write_iterations: int,
    read_iterations: int,
) -> None:
    lock = threading.Lock()
    shared_flow_id = flow_ids[0] if flow_ids else None
    reader_count = max(1, recipe.mixed_reader_count)
    reads_per_reader = max(1, read_iterations // reader_count)
    extra_reads = max(0, read_iterations - reads_per_reader * reader_count)
    stop = threading.Event()

    def writer() -> None:
        for i in range(write_iterations):
            if stop.is_set() or shared_flow_id is None:
                break
            task = _timed_call(
                latencies,
                "create_task",
                plane.create_task_run,
                shared_flow_id,
                f"mixed-task-{i}",
            )
            if recipe.fsm_task_lifecycle:
                n_events = _record_task_events_for_recipe(
                    plane, latencies, task.task_run_id, recipe, i
                )
            else:
                _timed_call(
                    latencies,
                    "record_task_event",
                    plane.record_task_event,
                    task.task_run_id,
                    "mixed",
                )
                n_events = 1
            with lock:
                counts["tasks_created"] += 1
                counts["task_events_recorded"] += n_events

    def reader(reader_idx: int) -> None:
        local_reads = reads_per_reader + (1 if reader_idx < extra_reads else 0)
        _measure_read_queries(
            plane,
            flow_ids,
            random.Random(rng.randint(0, 2**31)),
            latencies,
            local_reads,
        )
        with lock:
            counts["read_queries"] += local_reads

    wt = threading.Thread(target=writer, name="perf-writer")
    reader_threads = [
        threading.Thread(target=reader, args=(idx,), name=f"perf-reader-{idx}")
        for idx in range(reader_count)
    ]
    wt.start()
    for rt in reader_threads:
        rt.start()
    wt.join()
    for rt in reader_threads:
        rt.join()


def _run_recipe_iteration(
    recipe: WorkloadRecipe,
    seed: int,
    warmup: bool,
) -> RecipeRunSample:
    if recipe.decorator_hook_profile is not None:
        return _run_decorator_hook_micro_iteration(recipe, seed, warmup)
    if recipe.decorator_map_width is not None:
        return _run_decorator_map_micro_iteration(recipe, seed, warmup)
    if recipe.gcl_profile is not None:
        return _run_gcl_iteration(recipe, seed, warmup)
    if recipe.subflow_profile is not None:
        return _run_subflow_iteration(recipe, seed, warmup)

    rng = random.Random(seed)
    latencies: dict[str, list[float]] = {}
    counts = {
        "flows_created": 0,
        "tasks_created": 0,
        "flow_transitions": 0,
        "task_events_recorded": 0,
        "read_queries": 0,
    }
    notes: list[str] = []

    with tempfile.TemporaryDirectory(prefix="perf-matrix-") as td:
        history_path = Path(td) / "history.jsonl"
        db_path = history_path.with_suffix(".db")
        if not recipe.sqlite_enabled:
            history_path = None
            db_path = Path(td) / "unused.db"

        plane = InMemoryControlPlane(
            history_path=str(history_path) if history_path else None
        )
        try:
            if not recipe.cold_start:
                warm_flow = plane.create_flow_run("warmup-flow")
                plane.set_flow_state(
                    warm_flow.run_id, RunState.PENDING, uuid4(), "warmup"
                )
                plane.set_flow_state(
                    warm_flow.run_id, RunState.RUNNING, uuid4(), "warmup"
                )
                plane.set_flow_state(
                    warm_flow.run_id, RunState.COMPLETED, uuid4(), "warmup"
                )

            before_proc = _process_snapshot()
            sqlite_before = float(db_path.stat().st_size) if db_path.exists() else 0.0
            wal_before = (
                float((db_path.with_suffix(".db-wal")).stat().st_size)
                if db_path.with_suffix(".db-wal").exists()
                else 0.0
            )
            wall_start = time.perf_counter()

            flow_ids: list[Any] = []
            for idx in range(recipe.flow_count):
                flow = _timed_call(
                    latencies,
                    "create_flow",
                    plane.create_flow_run,
                    f"{recipe.name}-flow-{idx}",
                )
                flow_ids.append(flow.run_id)
                counts["flows_created"] += 1

                counts["flow_transitions"] += _apply_flow_start_transitions(
                    plane, latencies, flow.run_id
                )

                for task_idx in range(recipe.tasks_per_flow):
                    if recipe.fsm_task_lifecycle:
                        for evt in range(recipe.task_events_per_task):
                            task = _timed_call(
                                latencies,
                                "create_task",
                                plane.create_task_run,
                                flow.run_id,
                                f"task-{task_idx}-{evt}",
                            )
                            counts["tasks_created"] += 1
                            counts["task_events_recorded"] += (
                                _record_task_events_for_recipe(
                                    plane, latencies, task.task_run_id, recipe, evt
                                )
                            )
                    else:
                        task = _timed_call(
                            latencies,
                            "create_task",
                            plane.create_task_run,
                            flow.run_id,
                            f"task-{task_idx}",
                        )
                        counts["tasks_created"] += 1
                        for evt in range(recipe.task_events_per_task):
                            counts["task_events_recorded"] += (
                                _record_task_events_for_recipe(
                                    plane, latencies, task.task_run_id, recipe, evt
                                )
                            )

            read_count = int(
                (counts["tasks_created"] + counts["flows_created"]) * recipe.read_ratio
            )
            if recipe.mixed:
                write_iterations = max(10, recipe.flow_count // 2)
                read_iterations = max(10, read_count)
                _run_mixed_concurrent(
                    plane,
                    flow_ids,
                    rng,
                    latencies,
                    counts,
                    recipe,
                    write_iterations,
                    read_iterations,
                )
            else:
                _measure_read_queries(plane, flow_ids, rng, latencies, read_count)
                counts["read_queries"] += read_count

            wall_seconds = time.perf_counter() - wall_start
            after_proc = _process_snapshot()
            sqlite_after = (
                float(db_path.stat().st_size) if db_path.exists() else sqlite_before
            )
            wal_after = (
                float((db_path.with_suffix(".db-wal")).stat().st_size)
                if db_path.with_suffix(".db-wal").exists()
                else wal_before
            )
        finally:
            _close_plane_footprint(plane)

        total_writes = (
            counts["flows_created"]
            + counts["tasks_created"]
            + counts["flow_transitions"]
            + counts["task_events_recorded"]
        )
        sqlite_growth = max(0.0, sqlite_after - sqlite_before)
        write_amp = (sqlite_growth / float(total_writes)) if total_writes else 0.0

        throughput = {
            "flows_per_sec": counts["flows_created"] / wall_seconds
            if wall_seconds
            else 0.0,
            "tasks_per_sec": counts["tasks_created"] / wall_seconds
            if wall_seconds
            else 0.0,
            "transitions_per_sec": counts["flow_transitions"] / wall_seconds
            if wall_seconds
            else 0.0,
            "task_events_per_sec": counts["task_events_recorded"] / wall_seconds
            if wall_seconds
            else 0.0,
        }
        latency_ms = {
            name: _latency_stats_ms(values) for name, values in latencies.items()
        }
        process = {
            "cpu_seconds_used": max(
                0.0, after_proc.cpu_seconds - before_proc.cpu_seconds
            ),
            "rss_bytes_start": float(before_proc.rss_bytes),
            "rss_bytes_end": float(after_proc.rss_bytes),
            "rss_bytes_delta": float(after_proc.rss_bytes - before_proc.rss_bytes),
        }
        sqlite = {
            "db_bytes_before": sqlite_before,
            "db_bytes_after": sqlite_after,
            "db_bytes_growth": sqlite_growth,
            "wal_bytes_before": wal_before,
            "wal_bytes_after": wal_after,
            "wal_bytes_growth": max(0.0, wal_after - wal_before),
            "bytes_per_write_op": write_amp,
        }
        if not recipe.sqlite_enabled:
            notes.append("SQLite growth disabled for this recipe")

    sample = RecipeRunSample(
        recipe=recipe.name,
        iteration=0,
        warmup=warmup,
        seed=seed,
        wall_clock_seconds=wall_seconds,
        counts=counts,
        throughput=throughput,
        latency_ms=latency_ms,
        process=process,
        sqlite=sqlite,
    )
    if notes:
        sample.sqlite["notes"] = len(notes)
    return sample


def _run_recipe_task(
    recipe_name: str,
    repetitions: int,
    warmups: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    catalog = _recipe_catalog()
    if recipe_name not in catalog:
        raise ValueError(f"Unknown recipe: {recipe_name}")
    recipe = catalog[recipe_name]
    local_samples: list[dict[str, Any]] = []
    measured: list[RecipeRunSample] = []
    for warmup_idx in range(warmups):
        warm_seed = seed + (warmup_idx + 1) * 1000 + abs(hash(recipe_name)) % 997
        warm_sample = _run_recipe_iteration(recipe, warm_seed, warmup=True)
        warm_sample.iteration = warmup_idx + 1
        local_samples.append(asdict(warm_sample))
    for rep in range(repetitions):
        rep_seed = seed + (rep + 1) + abs(hash(recipe_name)) % 997
        sample = _run_recipe_iteration(recipe, rep_seed, warmup=False)
        sample.iteration = rep + 1
        measured.append(sample)
        local_samples.append(asdict(sample))
    return local_samples, asdict(aggregate_recipe(recipe, measured))


def aggregate_recipe(
    recipe: WorkloadRecipe, measured: list[RecipeRunSample]
) -> RecipeAggregate:
    wall_values = [s.wall_clock_seconds for s in measured]
    throughput_keys = sorted({k for s in measured for k in s.throughput})
    latency_keys = sorted({k for s in measured for k in s.latency_ms})
    process_keys = sorted({k for s in measured for k in s.process})
    sqlite_keys = sorted(
        {k for s in measured for k in s.sqlite if isinstance(s.sqlite[k], (int, float))}
    )

    throughput: dict[str, dict[str, float]] = {}
    for key in throughput_keys:
        vals = [s.throughput[key] for s in measured]
        throughput[key] = {
            "median": statistics.median(vals),
            "p95": _percentile(vals, 95),
            "p99": _percentile(vals, 99),
        }

    latency: dict[str, dict[str, float]] = {}
    for op in latency_keys:
        p50 = [float(s.latency_ms.get(op, {}).get("p50", 0.0)) for s in measured]
        p95 = [float(s.latency_ms.get(op, {}).get("p95", 0.0)) for s in measured]
        p99 = [float(s.latency_ms.get(op, {}).get("p99", 0.0)) for s in measured]
        latency[op] = {
            "p50": statistics.median(p50),
            "p95": statistics.median(p95),
            "p99": statistics.median(p99),
        }

    process: dict[str, dict[str, float]] = {}
    for key in process_keys:
        vals = [s.process[key] for s in measured]
        process[key] = {
            "median": statistics.median(vals),
            "p95": _percentile(vals, 95),
        }

    sqlite: dict[str, dict[str, float]] = {}
    for key in sqlite_keys:
        vals = [float(s.sqlite[key]) for s in measured]
        sqlite[key] = {
            "median": statistics.median(vals),
            "p95": _percentile(vals, 95),
        }

    return RecipeAggregate(
        recipe=recipe.name,
        repetitions=len(measured),
        params=asdict(recipe),
        wall_clock_seconds={
            "median": statistics.median(wall_values),
            "p95": _percentile(wall_values, 95),
            "p99": _percentile(wall_values, 99),
        },
        throughput=throughput,
        latency_ms=latency,
        process=process,
        sqlite=sqlite,
        notes=[],
    )


def _safe_path_get(payload: dict[str, Any], path: str) -> float:
    node: Any = payload
    for token in path.split("."):
        if not isinstance(node, dict) or token not in node:
            return 0.0
        node = node[token]
    if isinstance(node, (int, float)):
        return float(node)
    return 0.0


def flatten_aggregate(aggregate: RecipeAggregate) -> dict[str, float]:
    flat: dict[str, float] = {
        "wall_clock_seconds.p95": aggregate.wall_clock_seconds["p95"],
    }
    for key, stats in aggregate.throughput.items():
        flat[f"throughput.{key}.median"] = stats["median"]
        flat[f"throughput.{key}.p95"] = stats["p95"]
    for op, stats in aggregate.latency_ms.items():
        flat[f"latency_ms.{op}.p95"] = stats["p95"]
        flat[f"latency_ms.{op}.p99"] = stats["p99"]
    for key, stats in aggregate.process.items():
        flat[f"process.{key}.p95"] = stats["p95"]
    for key, stats in aggregate.sqlite.items():
        flat[f"sqlite.{key}.p95"] = stats["p95"]
    return flat


def compare_runs(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    thresholds: dict[str, float],
) -> dict[str, Any]:
    base_key = extract_matrix_compare_key(baseline)
    cand_key = extract_matrix_compare_key(candidate)
    if base_key != cand_key:
        return {
            "pass": False,
            "compatible": False,
            "compare_skipped": True,
            "reason": (
                "Benchmark mode mismatch: baseline and candidate are not comparable. "
                f"Baseline is {describe_matrix_compare_key(base_key)} (`{base_key}`), "
                f"candidate is {describe_matrix_compare_key(cand_key)} (`{cand_key}`). "
                "Capture a new baseline JSON using the same preset or the same `--recipes` list "
                "as the candidate run, then compare again."
            ),
            "baseline_compare_key": base_key,
            "candidate_compare_key": cand_key,
            "regressions": [],
            "comparisons": [],
        }

    baseline_rows = {row["recipe"]: row for row in baseline.get("aggregates", [])}
    candidate_rows = {row["recipe"]: row for row in candidate.get("aggregates", [])}
    all_recipes = sorted(set(baseline_rows) | set(candidate_rows))
    regressions: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []

    for recipe in all_recipes:
        base = baseline_rows.get(recipe)
        cand = candidate_rows.get(recipe)
        if base is None or cand is None:
            regressions.append(
                {
                    "recipe": recipe,
                    "metric": "missing_recipe",
                    "detail": "recipe missing in one side",
                    "regression": True,
                }
            )
            continue
        flat_base = flatten_aggregate(RecipeAggregate(**base))
        flat_cand = flatten_aggregate(RecipeAggregate(**cand))
        for metric, threshold in thresholds.items():
            b = flat_base.get(metric, 0.0)
            c = flat_cand.get(metric, 0.0)
            if b <= 0:
                continue
            delta_pct = ((c - b) / b) * 100.0
            better_is_higher = metric.startswith("throughput.")
            regression = (
                delta_pct < (-threshold * 100.0)
                if better_is_higher
                else delta_pct > (threshold * 100.0)
            )
            row = {
                "recipe": recipe,
                "metric": metric,
                "baseline": b,
                "candidate": c,
                "delta_pct": delta_pct,
                "threshold_pct": threshold * 100.0,
                "regression": regression,
            }
            comparisons.append(row)
            if regression:
                regressions.append(row)

    return {
        "pass": len(regressions) == 0,
        "compatible": True,
        "compare_skipped": False,
        "baseline_compare_key": base_key,
        "candidate_compare_key": cand_key,
        "regressions": regressions,
        "comparisons": comparisons,
    }


def build_compare_markdown(
    baseline_path: Path,
    candidate_path: Path,
    compare_result: dict[str, Any],
) -> str:
    if compare_result.get("compare_skipped"):
        lines = [
            "# Performance Regression Report",
            "",
            f"- Baseline: `{baseline_path.as_posix()}`",
            f"- Candidate: `{candidate_path.as_posix()}`",
            "- Status: `SKIP` (incompatible benchmark mode)",
            "",
            "## Compare not run",
            "",
            compare_result.get("reason", "Mode mismatch."),
            "",
            "| | Key |",
            "| --- | --- |",
            f"| Baseline | `{compare_result.get('baseline_compare_key', '')}` |",
            f"| Candidate | `{compare_result.get('candidate_compare_key', '')}` |",
            "",
            "Capture a baseline with `perf_matrix.py run` using the **same** `--preset` or **same** "
            "`--recipes` list as the candidate, then re-run `compare`.",
            "",
        ]
        return "\n".join(lines) + "\n"

    lines = [
        "# Performance Regression Report",
        "",
        f"- Baseline: `{baseline_path.as_posix()}`",
        f"- Candidate: `{candidate_path.as_posix()}`",
        f"- Benchmark mode: `{compare_result.get('baseline_compare_key', '')}`",
        f"- Status: `{'PASS' if compare_result['pass'] else 'FAIL'}`",
        "",
        "## Regressions",
        "",
        "| Recipe | Metric | Baseline | Candidate | Delta % | Threshold % |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    if compare_result["regressions"]:
        for row in compare_result["regressions"]:
            if row.get("metric") == "missing_recipe":
                lines.append(f"| {row['recipe']} | missing_recipe | - | - | - | - |")
                continue
            lines.append(
                f"| {row['recipe']} | {row['metric']} | {row['baseline']:.3f} | {row['candidate']:.3f} | "
                f"{row['delta_pct']:.2f}% | {row['threshold_pct']:.2f}% |"
            )
    else:
        lines.append("| - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## CI Summary",
            "",
            f"- compared metrics: `{len(compare_result['comparisons'])}`",
            f"- regressions: `{len(compare_result['regressions'])}`",
        ]
    )
    return "\n".join(lines) + "\n"


def run_suite(
    recipes: list[str],
    repetitions: int,
    warmups: int,
    seed: int,
    jobs: int,
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    aggregates: list[dict[str, Any]] = []

    if jobs <= 1:
        for recipe_name in recipes:
            local_samples, aggregate = _run_recipe_task(
                recipe_name, repetitions, warmups, seed
            )
            samples.extend(local_samples)
            aggregates.append(aggregate)
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = [
                pool.submit(_run_recipe_task, recipe_name, repetitions, warmups, seed)
                for recipe_name in recipes
            ]
            for future in concurrent.futures.as_completed(futures):
                local_samples, aggregate = future.result()
                samples.extend(local_samples)
                aggregates.append(aggregate)

    aggregates.sort(key=lambda row: str(row["recipe"]))
    samples.sort(
        key=lambda row: (str(row["recipe"]), int(row["iteration"]), bool(row["warmup"]))
    )

    matrix_compare_key = canonical_matrix_compare_key(recipes)
    matrix_mode = describe_matrix_compare_key(matrix_compare_key)

    return {
        "metadata": {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "git_sha": _git_sha(),
            "python_version": platform.python_version(),
            "rust_version": "unknown",
            "os": platform.platform(),
            "seed": seed,
            "repetitions": repetitions,
            "warmups": warmups,
            "jobs": jobs,
            "matrix_compare_key": matrix_compare_key,
            "matrix_mode": matrix_mode,
        },
        "recipes": recipes,
        "samples": samples,
        "aggregates": aggregates,
    }


def build_run_markdown(payload: dict[str, Any], out_json: Path) -> str:
    meta = payload.get("metadata") or {}
    lines = [
        "# IronFlow Deterministic Performance Matrix",
        "",
        f"- Generated: `{meta.get('timestamp_utc', '')}`",
        f"- Git SHA: `{meta.get('git_sha', '')}`",
        f"- OS: `{meta.get('os', '')}`",
        f"- Python: `{meta.get('python_version', '')}`",
    ]
    if meta.get("matrix_compare_key"):
        lines.append(
            f"- Benchmark mode: `{meta.get('matrix_mode', '')}` (`{meta.get('matrix_compare_key', '')}`)"
        )
    lines.extend(
        [
            f"- Raw JSON: `{out_json.as_posix()}`",
            "",
            "## Recipe Results",
            "",
            "| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload.get("aggregates", []):
        latency = row.get("latency_ms", {})
        read_p95 = max(
            float(latency.get("query.list_flow_runs", {}).get("p95", 0.0)),
            float(latency.get("query.list_task_runs", {}).get("p95", 0.0)),
            float(latency.get("query.list_events", {}).get("p95", 0.0)),
            float(latency.get("query.get_flow_run_detail", {}).get("p95", 0.0)),
        )
        lines.append(
            f"| {row['recipe']} | {row['wall_clock_seconds']['p95']:.3f} | "
            f"{row['throughput'].get('transitions_per_sec', {}).get('p95', 0.0):.2f} | "
            f"{latency.get('create_flow', {}).get('p95', 0.0):.3f} | "
            f"{latency.get('create_task', {}).get('p95', 0.0):.3f} | "
            f"{latency.get('set_flow_state', {}).get('p95', 0.0):.3f} | "
            f"{latency.get('record_task_event', {}).get('p95', 0.0):.3f} | "
            f"{read_p95:.3f} | "
            f"{row['process'].get('cpu_seconds_used', {}).get('p95', 0.0):.3f} | "
            f"{row['process'].get('rss_bytes_delta', {}).get('p95', 0.0):.0f} |"
        )

    lines.extend(
        [
            "",
            "## Anti-Flake Controls",
            "",
            "- Deterministic random seed per recipe/iteration.",
            "- Fixed recipe catalog with bounded sizes.",
            "- Warmup iterations are excluded from aggregates.",
            "- Metrics use medians/p95/p99 across multiple repetitions.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministic performance matrix runner and comparator."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Run deterministic benchmark suite.")
    run_cmd.add_argument("--preset", default="full", choices=sorted(_presets().keys()))
    run_cmd.add_argument(
        "--recipes", default="", help="Comma-separated recipe names (overrides preset)."
    )
    run_cmd.add_argument("--repetitions", type=int, default=5)
    run_cmd.add_argument("--warmups", type=int, default=2)
    run_cmd.add_argument("--seed", type=int, default=BASE_SEED)
    run_cmd.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of worker processes for recipe parallelism.",
    )
    run_cmd.add_argument(
        "--out-json",
        default=str(ROOT / "docs" / "perf_matrix_results.json"),
    )
    run_cmd.add_argument(
        "--out-md",
        default=str(ROOT / "docs" / "perf_matrix_summary.md"),
    )

    cmp_cmd = sub.add_parser(
        "compare",
        help=(
            "Compare two JSON files from `run` (same benchmark mode only). "
            "Each file records `metadata.matrix_compare_key` (preset or recipe set); "
            "if keys differ, compare is skipped with a clear message (exit 3)."
        ),
    )
    cmp_cmd.add_argument("--baseline", required=True)
    cmp_cmd.add_argument("--candidate", required=True)
    cmp_cmd.add_argument(
        "--thresholds",
        default="latency_ms.create_flow.p95=0.10,latency_ms.set_flow_state.p95=0.10,throughput.transitions_per_sec.median=0.10,wall_clock_seconds.p95=0.10",
        help="Comma list metric=ratio threshold.",
    )
    cmp_cmd.add_argument(
        "--out-json", default=str(ROOT / "docs" / "perf_compare_report.json")
    )
    cmp_cmd.add_argument(
        "--out-md", default=str(ROOT / "docs" / "perf_compare_report.md")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "run":
        recipes = (
            parse_recipe_list(args.recipes) if args.recipes else _presets()[args.preset]
        )
        payload = run_suite(
            recipes=recipes,
            repetitions=args.repetitions,
            warmups=args.warmups,
            seed=args.seed,
            jobs=max(1, args.jobs),
        )
        out_json = Path(args.out_json)
        out_md = Path(args.out_md)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        out_md.write_text(build_run_markdown(payload, out_json), encoding="utf-8")
        print(f"wrote run JSON: {out_json}")
        print(f"wrote run report: {out_md}")
        mk = payload.get("metadata") or {}
        if mk.get("matrix_compare_key"):
            print(
                f"benchmark mode: {mk.get('matrix_compare_key')} ({mk.get('matrix_mode', '')})"
            )
        return 0

    try:
        baseline = load_matrix_run_json(Path(args.baseline))
        candidate = load_matrix_run_json(Path(args.candidate))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    thresholds = parse_thresholds(args.thresholds)
    result = compare_runs(baseline, candidate, thresholds)

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    out_md.write_text(
        build_compare_markdown(Path(args.baseline), Path(args.candidate), result),
        encoding="utf-8",
    )

    if result.get("compare_skipped"):
        print(f"SKIP (incompatible mode): {result.get('reason', '')}", file=sys.stderr)
        print(f"report json: {out_json}")
        print(f"report md: {out_md}")
        return 3

    status = "PASS" if result["pass"] else "FAIL"
    print(
        f"{status}: regressions={len(result['regressions'])} comparisons={len(result['comparisons'])}"
    )
    print(f"report json: {out_json}")
    print(f"report md: {out_md}")
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
