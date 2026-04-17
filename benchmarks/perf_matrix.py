from __future__ import annotations

import argparse
import ctypes
import concurrent.futures
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PYTHON_SHIM_SRC = ROOT / "python-shim" / "src"
if str(PYTHON_SHIM_SRC) not in sys.path:
    sys.path.insert(0, str(PYTHON_SHIM_SRC))

from prefect_compat.runtime import InMemoryControlPlane, RunState

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
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        if ok:
            return int(counters.WorkingSetSize)
        return 0
    try:
        import resource  # type: ignore

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
        "full": [k for k in _recipe_catalog().keys() if not k.startswith("micro_decorator_hooks_")],
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

        plane = InMemoryControlPlane(history_path=str(history_path) if history_path else None)
        sqlite_before = float(db_path.stat().st_size) if db_path.exists() else 0.0
        wal_path = db_path.with_suffix(".db-wal")
        wal_before = float(wal_path.stat().st_size) if wal_path.exists() else 0.0
        before_proc = _process_snapshot()

        from prefect_compat import flow, on_transition, set_control_plane, task

        set_control_plane(plane)
        noop = on_transition(_noop_transition_hook)

        if profile == "none":

            @task
            def work(x: int) -> int:
                return x + 1

            @flow
            def sample() -> int:
                return work.submit(1).result()

        elif profile == "flow":

            @task
            def work(x: int) -> int:
                return x + 1

            @flow(transition_hooks=[noop])
            def sample() -> int:
                return work.submit(1).result()

        elif profile == "task":

            @task(transition_hooks=[noop])
            def work(x: int) -> int:
                return x + 1

            @flow
            def sample() -> int:
                return work.submit(1).result()

        else:

            @task(transition_hooks=[noop])
            def work(x: int) -> int:
                return x + 1

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
        sqlite_after = float(db_path.stat().st_size) if db_path.exists() else sqlite_before
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


def _measure_read_queries(
    plane: InMemoryControlPlane,
    flow_ids: list[Any],
    rng: random.Random,
    latencies: dict[str, list[float]],
    reads: int,
) -> None:
    for _ in range(reads):
        choice = rng.random()
        if choice < 0.34:
            _timed_call(latencies, "query.list_flow_runs", plane.list_flow_runs, 200, None)
        elif choice < 0.67:
            _timed_call(latencies, "query.list_task_runs", plane.list_task_runs, 200, None)
        else:
            _timed_call(latencies, "query.list_events", plane.list_events, 200, None)
        if flow_ids and rng.random() < 0.4:
            rid = flow_ids[rng.randrange(len(flow_ids))]
            _timed_call(latencies, "query.get_flow_run_detail", plane.get_flow_run_detail, rid)


def _run_recipe_iteration(
    recipe: WorkloadRecipe,
    seed: int,
    warmup: bool,
) -> RecipeRunSample:
    if recipe.decorator_hook_profile is not None:
        return _run_decorator_hook_micro_iteration(recipe, seed, warmup)

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

        plane = InMemoryControlPlane(history_path=str(history_path) if history_path else None)
        try:
            if not recipe.cold_start:
                warm_flow = plane.create_flow_run("warmup-flow")
                plane.set_flow_state(warm_flow.run_id, RunState.PENDING, uuid4(), "warmup")
                plane.set_flow_state(warm_flow.run_id, RunState.RUNNING, uuid4(), "warmup")
                plane.set_flow_state(warm_flow.run_id, RunState.COMPLETED, uuid4(), "warmup")

            before_proc = _process_snapshot()
            sqlite_before = float(db_path.stat().st_size) if db_path.exists() else 0.0
            wal_before = (
                float((db_path.with_suffix(".db-wal")).stat().st_size) if db_path.with_suffix(".db-wal").exists() else 0.0
            )
            wall_start = time.perf_counter()

            flow_ids: list[Any] = []
            for idx in range(recipe.flow_count):
                flow = _timed_call(latencies, "create_flow", plane.create_flow_run, f"{recipe.name}-flow-{idx}")
                flow_ids.append(flow.run_id)
                counts["flows_created"] += 1

                _timed_call(latencies, "set_flow_state", plane.set_flow_state, flow.run_id, RunState.PENDING, uuid4(), "bench")
                _timed_call(latencies, "set_flow_state", plane.set_flow_state, flow.run_id, RunState.RUNNING, uuid4(), "bench")
                _timed_call(latencies, "set_flow_state", plane.set_flow_state, flow.run_id, RunState.COMPLETED, uuid4(), "bench")
                counts["flow_transitions"] += 3

                for task_idx in range(recipe.tasks_per_flow):
                    task = _timed_call(
                        latencies,
                        "create_task",
                        plane.create_task_run,
                        flow.run_id,
                        f"task-{task_idx}",
                    )
                    counts["tasks_created"] += 1
                    for evt in range(recipe.task_events_per_task):
                        _timed_call(
                            latencies,
                            "record_task_event",
                            plane.record_task_event,
                            task.task_run_id,
                            "heartbeat",
                            {"ordinal": evt},
                        )
                        counts["task_events_recorded"] += 1

            read_count = int((counts["tasks_created"] + counts["flows_created"]) * recipe.read_ratio)
            if recipe.mixed:
                write_iterations = max(10, recipe.flow_count // 2)
                read_iterations = max(10, read_count)
                lock = threading.Lock()
                shared_flow_id = flow_ids[0] if flow_ids else None

                def writer() -> None:
                    for i in range(write_iterations):
                        if shared_flow_id is None:
                            break
                        task = _timed_call(latencies, "create_task", plane.create_task_run, shared_flow_id, f"mixed-task-{i}")
                        _timed_call(latencies, "record_task_event", plane.record_task_event, task.task_run_id, "mixed")
                        with lock:
                            counts["tasks_created"] += 1
                            counts["task_events_recorded"] += 1

                def reader() -> None:
                    _measure_read_queries(plane, flow_ids, rng, latencies, read_iterations)
                    with lock:
                        counts["read_queries"] += read_iterations

                wt = threading.Thread(target=writer, name="perf-writer")
                rt = threading.Thread(target=reader, name="perf-reader")
                wt.start()
                rt.start()
                wt.join()
                rt.join()
            else:
                _measure_read_queries(plane, flow_ids, rng, latencies, read_count)
                counts["read_queries"] += read_count

            wall_seconds = time.perf_counter() - wall_start
            after_proc = _process_snapshot()
            sqlite_after = float(db_path.stat().st_size) if db_path.exists() else sqlite_before
            wal_after = (
                float((db_path.with_suffix(".db-wal")).stat().st_size) if db_path.with_suffix(".db-wal").exists() else wal_before
            )
        finally:
            _close_plane_footprint(plane)

        total_writes = counts["flows_created"] + counts["tasks_created"] + counts["flow_transitions"] + counts["task_events_recorded"]
        sqlite_growth = max(0.0, sqlite_after - sqlite_before)
        write_amp = (sqlite_growth / float(total_writes)) if total_writes else 0.0

        throughput = {
            "flows_per_sec": counts["flows_created"] / wall_seconds if wall_seconds else 0.0,
            "tasks_per_sec": counts["tasks_created"] / wall_seconds if wall_seconds else 0.0,
            "transitions_per_sec": counts["flow_transitions"] / wall_seconds if wall_seconds else 0.0,
            "task_events_per_sec": counts["task_events_recorded"] / wall_seconds if wall_seconds else 0.0,
        }
        latency_ms = {name: _latency_stats_ms(values) for name, values in latencies.items()}
        process = {
            "cpu_seconds_used": max(0.0, after_proc.cpu_seconds - before_proc.cpu_seconds),
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


def aggregate_recipe(recipe: WorkloadRecipe, measured: list[RecipeRunSample]) -> RecipeAggregate:
    wall_values = [s.wall_clock_seconds for s in measured]
    throughput_keys = sorted({k for s in measured for k in s.throughput})
    latency_keys = sorted({k for s in measured for k in s.latency_ms})
    process_keys = sorted({k for s in measured for k in s.process})
    sqlite_keys = sorted({k for s in measured for k in s.sqlite if isinstance(s.sqlite[k], (int, float))})

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
                {"recipe": recipe, "metric": "missing_recipe", "detail": "recipe missing in one side", "regression": True}
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
            regression = delta_pct < (-threshold * 100.0) if better_is_higher else delta_pct > (threshold * 100.0)
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
    catalog = _recipe_catalog()
    samples: list[dict[str, Any]] = []
    aggregates: list[dict[str, Any]] = []

    if jobs <= 1:
        for recipe_name in recipes:
            local_samples, aggregate = _run_recipe_task(recipe_name, repetitions, warmups, seed)
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
    samples.sort(key=lambda row: (str(row["recipe"]), int(row["iteration"]), bool(row["warmup"])))

    matrix_compare_key = canonical_matrix_compare_key(recipes)
    matrix_mode = describe_matrix_compare_key(matrix_compare_key)

    return {
        "metadata": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
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
    parser = argparse.ArgumentParser(description="Deterministic performance matrix runner and comparator.")
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Run deterministic benchmark suite.")
    run_cmd.add_argument("--preset", default="full", choices=sorted(_presets().keys()))
    run_cmd.add_argument("--recipes", default="", help="Comma-separated recipe names (overrides preset).")
    run_cmd.add_argument("--repetitions", type=int, default=5)
    run_cmd.add_argument("--warmups", type=int, default=2)
    run_cmd.add_argument("--seed", type=int, default=BASE_SEED)
    run_cmd.add_argument("--jobs", type=int, default=1, help="Number of worker processes for recipe parallelism.")
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
    cmp_cmd.add_argument("--out-json", default=str(ROOT / "docs" / "perf_compare_report.json"))
    cmp_cmd.add_argument("--out-md", default=str(ROOT / "docs" / "perf_compare_report.md"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "run":
        recipes = parse_recipe_list(args.recipes) if args.recipes else _presets()[args.preset]
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
            print(f"benchmark mode: {mk.get('matrix_compare_key')} ({mk.get('matrix_mode', '')})")
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
    print(f"{status}: regressions={len(result['regressions'])} comparisons={len(result['comparisons'])}")
    print(f"report json: {out_json}")
    print(f"report md: {out_md}")
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


def _wait_for_url(url: str, timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=1.5) as client:
                response = client.get(url)
            if response.status_code < 500:
                return
        except Exception:
            pass
        time.sleep(0.4)
    raise TimeoutError(f"Timed out waiting for {url}")


def _probe_latency(url: str, calls: int = 9) -> float:
    timings: list[float] = []
    with httpx.Client(timeout=10.0) as client:
        for _ in range(calls):
            start = time.perf_counter()
            response = client.get(url)
            response.raise_for_status()
            timings.append(time.perf_counter() - start)
    if not timings:
        return 0.0
    return statistics.quantiles(timings, n=20, method="inclusive")[18]


def _estimate_transitions(flavor: str, complexity: int) -> int:
    # Coarse cross-engine comparable estimate (run + each task lifecycle).
    if flavor == "simple":
        task_count = 2
    else:
        task_count = complexity + 1
    return 3 + (task_count * 3)


def _ensure_frontend_built(frontend_dir: Path, npm_cmd: list[str]) -> tuple[bool, str]:
    dist_index = frontend_dir / "dist" / "index.html"
    if dist_index.exists():
        return True, ""
    proc = subprocess.run(
        [*npm_cmd, "run", "build"],
        cwd=str(frontend_dir),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-500:]
        return False, tail
    return dist_index.exists(), ""


def _resolve_npm_command() -> list[str] | None:
    npm = shutil.which("npm")
    if npm:
        return [npm]
    windows_fallback = Path(r"C:\Program Files\nodejs\npm.cmd")
    if windows_fallback.exists():
        return [str(windows_fallback)]
    return None


def _run_ironflow_inproc(flavor: str, complexity: int, iteration: int) -> PerfSample:
    tmpdir = tempfile.mkdtemp(prefix="ironflow-perf-")
    old_cwd = Path.cwd()
    try:
        os.chdir(tmpdir)
        sys.path.insert(0, str(ROOT / "python-shim" / "src"))
        from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task, wait
        from prefect_compat.task_runners import ThreadPoolTaskRunner

        plane = InMemoryControlPlane()
        start_boot = time.perf_counter()
        set_control_plane(plane)
        startup = time.perf_counter() - start_boot

        @task
        def inc(x: int) -> int:
            return x + 1

        @task
        def dbl(x: int) -> int:
            return x * 2

        @task
        def passthrough(x: int) -> int:
            return x

        @flow
        def simple(n: int) -> int:
            first = inc.submit(n)
            second = dbl.submit(first, wait_for=[first])
            return second.result()

        @flow(task_runner=ThreadPoolTaskRunner())
        def wide(n: int) -> int:
            first = inc.submit(n)
            mapped_futs = dbl.map(range(n), wait_for=[first])
            wait(mapped_futs)
            return sum(f.result() for f in mapped_futs)

        @flow
        def long_chain(n: int) -> int:
            f = passthrough.submit(0)
            for _ in range(n):
                f = inc.submit(f, wait_for=[f])
            return f.result()

        flow_map: dict[str, Callable[[int], int]] = {
            "simple": simple,
            "wide": wide,
            "long_chain": long_chain,
            # Backwards-compatible aliases.
            "mapped": wide,
            "chained": long_chain,
        }
        flow_fn = flow_map.get(flavor)
        if flow_fn is None:
            raise ValueError(f"Unsupported flavor: {flavor}")
        t0 = time.perf_counter()
        _ = flow_fn(complexity)
        runtime = time.perf_counter() - t0
        events = len(plane.events())
    finally:
        os.chdir(old_cwd)

    return PerfSample(
        scenario="ironflow_inproc",
        engine="ironflow",
        backend_enabled=False,
        ui_enabled=False,
        flavor=flavor,
        complexity=complexity,
        iteration=iteration,
        backend_startup_seconds=startup,
        ui_startup_seconds=0.0,
        runtime_seconds=runtime,
        transition_events=events,
        transitions_per_second=(events / runtime) if runtime > 0 else 0.0,
        api_p95_seconds=0.0,
        ui_probe_seconds=0.0,
    )


def _run_ironflow_service_mode(
    flavor: str,
    complexity: int,
    iteration: int,
    with_ui: bool,
    backend_port: int,
    frontend_port: int,
) -> PerfSample:
    env = os.environ.copy()
    python_path_parts = [str(ROOT / "python-shim" / "src")]
    if env.get("PYTHONPATH"):
        python_path_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path_parts)

    notes: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        env["IRONFLOW_HISTORY_PATH"] = str(Path(tmpdir) / "history.jsonl")

        backend_cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "prefect_compat.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(backend_port),
        ]
        backend_start = time.perf_counter()
        backend = subprocess.Popen(
            backend_cmd,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ui = None
        ui_startup = 0.0
        api_p95 = 0.0
        ui_probe = 0.0
        runtime = 0.0
        events = 0
        try:
            _wait_for_url(f"http://127.0.0.1:{backend_port}/health", timeout_s=60)
            backend_startup = time.perf_counter() - backend_start

            if with_ui:
                npm_cmd = _resolve_npm_command()
                if npm_cmd is None:
                    notes.append("npm unavailable: skipping UI startup and probe")
                else:
                    frontend_dir = ROOT / "frontend"
                    ok_build, build_err = _ensure_frontend_built(frontend_dir, npm_cmd)
                    if not ok_build:
                        notes.append(f"frontend build failed (vite preview requires dist): {build_err[:200]}")
                    else:
                        ui_env = dict(env, PORT=str(frontend_port))
                        ui_start = time.perf_counter()
                        ui = subprocess.Popen(
                            [
                                *npm_cmd,
                                "run",
                                "preview",
                                "--",
                                "--host",
                                "127.0.0.1",
                                "--port",
                                str(frontend_port),
                                "--strictPort",
                            ],
                            cwd=str(frontend_dir),
                            env=ui_env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        _wait_for_url(f"http://127.0.0.1:{frontend_port}/runs", timeout_s=120)
                        ui_startup = time.perf_counter() - ui_start
                        ui_probe = _probe_latency(f"http://127.0.0.1:{frontend_port}/runs", calls=5)

            try:
                with httpx.Client(timeout=300.0) as client:
                    before = client.get(f"http://127.0.0.1:{backend_port}/history/summary").json().get("events", 0)
                    response = client.post(
                        f"http://127.0.0.1:{backend_port}/benchmark/run",
                        json={"flavor": flavor, "complexity": complexity},
                    )
                    response.raise_for_status()
                    body = response.json()
                    after = client.get(f"http://127.0.0.1:{backend_port}/history/summary").json().get("events", 0)
                events = max(after - before, 0)
                runtime = float(body["runtime_seconds"])
                api_p95 = _probe_latency(f"http://127.0.0.1:{backend_port}/api/flow-runs?limit=50")
            except Exception as exc:
                notes.append(f"benchmark request failed: {exc}")

            return PerfSample(
                scenario="ironflow_backend_ui" if with_ui else "ironflow_backend",
                engine="ironflow",
                backend_enabled=True,
                ui_enabled=with_ui,
                flavor=flavor,
                complexity=complexity,
                iteration=iteration,
                backend_startup_seconds=backend_startup,
                ui_startup_seconds=ui_startup,
                runtime_seconds=runtime,
                transition_events=events,
                transitions_per_second=(events / runtime) if runtime > 0 else 0.0,
                api_p95_seconds=api_p95,
                ui_probe_seconds=ui_probe,
                notes="; ".join(notes),
            )
        except Exception as exc:
            notes.append(f"service scenario failed: {exc}")
            return PerfSample(
                scenario="ironflow_backend_ui" if with_ui else "ironflow_backend",
                engine="ironflow",
                backend_enabled=True,
                ui_enabled=with_ui,
                flavor=flavor,
                complexity=complexity,
                iteration=iteration,
                backend_startup_seconds=0.0,
                ui_startup_seconds=0.0,
                runtime_seconds=0.0,
                transition_events=0,
                transitions_per_second=0.0,
                api_p95_seconds=0.0,
                ui_probe_seconds=0.0,
                notes="; ".join(notes),
            )
        finally:
            if ui is not None:
                ui.terminate()
                try:
                    ui.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    ui.kill()
            backend.terminate()
            try:
                backend.wait(timeout=10)
            except subprocess.TimeoutExpired:
                backend.kill()


def _run_prefect_inproc(flavor: str, complexity: int, iteration: int) -> PerfSample:
    os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
    try:
        from prefect import flow, task  # type: ignore
    except Exception as exc:
        return PerfSample(
            scenario="prefect_inproc",
            engine="prefect",
            backend_enabled=False,
            ui_enabled=False,
            flavor=flavor,
            complexity=complexity,
            iteration=iteration,
            backend_startup_seconds=0.0,
            ui_startup_seconds=0.0,
            runtime_seconds=0.0,
            transition_events=0,
            transitions_per_second=0.0,
            api_p95_seconds=0.0,
            ui_probe_seconds=0.0,
            notes=f"prefect import unavailable: {exc}",
        )

    @task
    def inc(x: int) -> int:
        return x + 1

    @task
    def dbl(x: int) -> int:
        return x * 2

    @task
    def passthrough(x: int) -> int:
        return x

    @flow
    def simple(n: int) -> int:
        first = inc.submit(n)
        second = dbl.submit(first, wait_for=[first])
        return second.result()

    @flow
    def wide(n: int) -> int:
        first = inc.submit(n)
        mapped_futs = dbl.map(range(n), wait_for=[first])
        return sum(f.result() for f in mapped_futs)

    @flow
    def long_chain(n: int) -> int:
        f = passthrough.submit(0)
        for _ in range(n):
            f = inc.submit(f, wait_for=[f])
        return f.result()

    flow_map: dict[str, Callable[[int], int]] = {
        "simple": simple,
        "wide": wide,
        "long_chain": long_chain,
        # Backwards-compatible aliases.
        "mapped": wide,
        "chained": long_chain,
    }
    flow_fn = flow_map.get(flavor)
    if flow_fn is None:
        raise ValueError(f"Unsupported flavor: {flavor}")
    t0 = time.perf_counter()
    _ = flow_fn(complexity)
    runtime = time.perf_counter() - t0
    transitions = _estimate_transitions(flavor, complexity)
    return PerfSample(
        scenario="prefect_inproc",
        engine="prefect",
        backend_enabled=False,
        ui_enabled=False,
        flavor=flavor,
        complexity=complexity,
        iteration=iteration,
        backend_startup_seconds=0.0,
        ui_startup_seconds=0.0,
        runtime_seconds=runtime,
        transition_events=transitions,
        transitions_per_second=(transitions / runtime) if runtime > 0 else 0.0,
        api_p95_seconds=0.0,
        ui_probe_seconds=0.0,
    )


def _run_prefect_server_mode(
    flavor: str,
    complexity: int,
    iteration: int,
    port: int,
    with_ui_probe: bool,
) -> PerfSample:
    os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
    try:
        from prefect import flow, task  # type: ignore
    except Exception as exc:
        return PerfSample(
            scenario="prefect_server_ui" if with_ui_probe else "prefect_server",
            engine="prefect",
            backend_enabled=True,
            ui_enabled=with_ui_probe,
            flavor=flavor,
            complexity=complexity,
            iteration=iteration,
            backend_startup_seconds=0.0,
            ui_startup_seconds=0.0,
            runtime_seconds=0.0,
            transition_events=0,
            transitions_per_second=0.0,
            api_p95_seconds=0.0,
            ui_probe_seconds=0.0,
            notes=f"prefect import unavailable: {exc}",
        )

    env = os.environ.copy()
    env["PREFECT_API_URL"] = f"http://127.0.0.1:{port}/api"
    server_cmd = ["prefect", "server", "start", "--host", "127.0.0.1", "--port", str(port)]
    start = time.perf_counter()
    try:
        server = subprocess.Popen(
            server_cmd,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return PerfSample(
            scenario="prefect_server_ui" if with_ui_probe else "prefect_server",
            engine="prefect",
            backend_enabled=True,
            ui_enabled=with_ui_probe,
            flavor=flavor,
            complexity=complexity,
            iteration=iteration,
            backend_startup_seconds=0.0,
            ui_startup_seconds=0.0,
            runtime_seconds=0.0,
            transition_events=0,
            transitions_per_second=0.0,
            api_p95_seconds=0.0,
            ui_probe_seconds=0.0,
            notes=f"prefect server command unavailable: {exc}",
        )

    try:
        _wait_for_url(f"http://127.0.0.1:{port}/api/health", timeout_s=120)
        startup = time.perf_counter() - start
        ui_probe = 0.0
        if with_ui_probe:
            ui_probe = _probe_latency(f"http://127.0.0.1:{port}/", calls=5)

        @task
        def inc(x: int) -> int:
            return x + 1

        @task
        def dbl(x: int) -> int:
            return x * 2

        @task
        def passthrough(x: int) -> int:
            return x

        @flow
        def simple(n: int) -> int:
            first = inc.submit(n)
            second = dbl.submit(first, wait_for=[first])
            return second.result()

        @flow
        def wide(n: int) -> int:
            first = inc.submit(n)
            mapped_futs = dbl.map(range(n), wait_for=[first])
            return sum(f.result() for f in mapped_futs)

        @flow
        def long_chain(n: int) -> int:
            f = passthrough.submit(0)
            for _ in range(n):
                f = inc.submit(f, wait_for=[f])
            return f.result()

        flow_map: dict[str, Callable[[int], int]] = {
            "simple": simple,
            "wide": wide,
            "long_chain": long_chain,
            # Backwards-compatible aliases.
            "mapped": wide,
            "chained": long_chain,
        }
        flow_fn = flow_map.get(flavor)
        if flow_fn is None:
            raise ValueError(f"Unsupported flavor: {flavor}")
        t0 = time.perf_counter()
        _ = flow_fn(complexity)
        runtime = time.perf_counter() - t0
        transitions = _estimate_transitions(flavor, complexity)
        api_p95 = _probe_latency(f"http://127.0.0.1:{port}/api/health", calls=5)
        return PerfSample(
            scenario="prefect_server_ui" if with_ui_probe else "prefect_server",
            engine="prefect",
            backend_enabled=True,
            ui_enabled=with_ui_probe,
            flavor=flavor,
            complexity=complexity,
            iteration=iteration,
            backend_startup_seconds=startup,
            ui_startup_seconds=0.0,
            runtime_seconds=runtime,
            transition_events=transitions,
            transitions_per_second=(transitions / runtime) if runtime > 0 else 0.0,
            api_p95_seconds=api_p95,
            ui_probe_seconds=ui_probe,
        )
    except Exception as exc:
        return PerfSample(
            scenario="prefect_server_ui" if with_ui_probe else "prefect_server",
            engine="prefect",
            backend_enabled=True,
            ui_enabled=with_ui_probe,
            flavor=flavor,
            complexity=complexity,
            iteration=iteration,
            backend_startup_seconds=0.0,
            ui_startup_seconds=0.0,
            runtime_seconds=0.0,
            transition_events=0,
            transitions_per_second=0.0,
            api_p95_seconds=0.0,
            ui_probe_seconds=0.0,
            notes=f"prefect server benchmark failed: {exc}",
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def _aggregate(samples: list[PerfSample]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, int], list[PerfSample]] = {}
    for sample in samples:
        grouped.setdefault((sample.scenario, sample.flavor, sample.complexity), []).append(sample)

    rows: list[dict[str, object]] = []
    for (scenario, flavor, complexity), bucket in sorted(grouped.items()):
        runtime_values = [s.runtime_seconds for s in bucket if s.runtime_seconds > 0]
        throughput_values = [s.transitions_per_second for s in bucket if s.transitions_per_second > 0]
        api_values = [s.api_p95_seconds for s in bucket if s.api_p95_seconds > 0]
        ui_values = [s.ui_probe_seconds for s in bucket if s.ui_probe_seconds > 0]
        startup_values = [s.backend_startup_seconds for s in bucket if s.backend_startup_seconds > 0]
        rows.append(
            {
                "scenario": scenario,
                "flavor": flavor,
                "complexity": complexity,
                "runs": len(bucket),
                "runtime_median_s": statistics.median(runtime_values) if runtime_values else 0.0,
                "runtime_p95_s": statistics.quantiles(runtime_values, n=20, method="inclusive")[18]
                if len(runtime_values) > 1
                else (runtime_values[0] if runtime_values else 0.0),
                "throughput_median_tps": statistics.median(throughput_values) if throughput_values else 0.0,
                "backend_startup_median_s": statistics.median(startup_values) if startup_values else 0.0,
                "api_p95_median_s": statistics.median(api_values) if api_values else 0.0,
                "ui_probe_median_s": statistics.median(ui_values) if ui_values else 0.0,
                "notes": "; ".join(sorted({s.notes for s in bucket if s.notes})),
            }
        )
    return rows


def _build_markdown(rows: list[dict[str, object]], out_json: Path) -> str:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Performance Matrix: IronFlow vs Prefect",
        "",
        f"Generated at: `{now}`",
        f"Raw samples: `{out_json.as_posix()}`",
        "",
        "## Scenarios",
        "",
        "- `ironflow_inproc`: no backend, no UI",
        "- `ironflow_backend`: backend only (`uvicorn`)",
        "- `ironflow_backend_ui`: backend + Vite UI",
        "- `prefect_inproc`: direct flow execution without external server",
        "- `prefect_server`: Prefect server/API",
        "- `prefect_server_ui`: Prefect server/API + UI probe",
        "",
        "## Workload Shapes",
        "",
        "- `simple`: two-task dependency (`inc -> dbl`)",
        "- `wide`: one gate task plus fan-out mapped tasks",
        "- `long_chain`: strict serial dependency chain",
        "",
        "## Aggregated Results",
        "",
        "| Scenario | Flavor | N | Runtime median (s) | Runtime p95 (s) | Throughput median (events/s) | Backend startup median (s) | API p95 median (s) | UI probe median (s) | Notes |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['scenario']} | {row['flavor']}:{row['complexity']} | {row['runs']} | "
            f"{row['runtime_median_s']:.4f} | {row['runtime_p95_s']:.4f} | {row['throughput_median_tps']:.2f} | "
            f"{row['backend_startup_median_s']:.4f} | {row['api_p95_median_s']:.4f} | {row['ui_probe_median_s']:.4f} | "
            f"{row['notes'] or '-'} |"
        )
    lines.extend(
        [
            "",
            "## Speedup Highlights",
            "",
        ]
    )
    lines.extend(_build_speedup_section(rows))
    lines.extend(
        [
            "",
            "## Interpretation Guide",
            "",
            "- Compare `runtime_median_s` for flow execution overhead.",
            "- Compare `backend_startup_median_s` for cold-start costs.",
            "- Compare `api_p95_median_s` and `ui_probe_median_s` for service/UI responsiveness.",
            "- If a row has notes, treat that scenario as partial or skipped.",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_speedup_section(rows: list[dict[str, object]]) -> list[str]:
    lines = [
        "| Mode | Flavor | Runtime winner | Runtime speedup | Throughput winner | Throughput speedup |",
        "| --- | --- | --- | ---: | --- | ---: |",
    ]

    mode_pairs = [
        ("inproc", "ironflow_inproc", "prefect_inproc"),
        ("backend", "ironflow_backend", "prefect_server"),
        ("backend+ui", "ironflow_backend_ui", "prefect_server_ui"),
    ]

    row_index: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        row_index[(str(row["scenario"]), str(row["flavor"]))] = row

    for mode_name, ironflow_scenario, prefect_scenario in mode_pairs:
        all_flavors = sorted(
            {
                flavor
                for (scenario, flavor) in row_index
                if scenario in {ironflow_scenario, prefect_scenario}
            }
        )
        for flavor in all_flavors:
            ironflow_row = row_index.get((ironflow_scenario, flavor))
            prefect_row = row_index.get((prefect_scenario, flavor))
            if ironflow_row is None or prefect_row is None:
                lines.append(f"| {mode_name} | {flavor} | n/a | n/a | n/a | n/a |")
                continue

            ironflow_runtime = float(ironflow_row["runtime_median_s"])
            prefect_runtime = float(prefect_row["runtime_median_s"])
            ironflow_tps = float(ironflow_row["throughput_median_tps"])
            prefect_tps = float(prefect_row["throughput_median_tps"])

            runtime_winner = "n/a"
            runtime_speedup = "n/a"
            if ironflow_runtime > 0 and prefect_runtime > 0:
                if ironflow_runtime < prefect_runtime:
                    runtime_winner = "ironflow"
                    runtime_speedup = f"{(prefect_runtime / ironflow_runtime):.2f}x"
                elif prefect_runtime < ironflow_runtime:
                    runtime_winner = "prefect"
                    runtime_speedup = f"{(ironflow_runtime / prefect_runtime):.2f}x"
                else:
                    runtime_winner = "tie"
                    runtime_speedup = "1.00x"

            throughput_winner = "n/a"
            throughput_speedup = "n/a"
            if ironflow_tps > 0 and prefect_tps > 0:
                if ironflow_tps > prefect_tps:
                    throughput_winner = "ironflow"
                    throughput_speedup = f"{(ironflow_tps / prefect_tps):.2f}x"
                elif prefect_tps > ironflow_tps:
                    throughput_winner = "prefect"
                    throughput_speedup = f"{(prefect_tps / ironflow_tps):.2f}x"
                else:
                    throughput_winner = "tie"
                    throughput_speedup = "1.00x"

            lines.append(
                f"| {mode_name} | {flavor} | {runtime_winner} | {runtime_speedup} | "
                f"{throughput_winner} | {throughput_speedup} |"
            )

    return lines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run comprehensive performance matrix across inproc/backend/UI modes."
    )
    parser.add_argument("--iterations", type=int, default=5, help="Repetitions per scenario/workload.")
    parser.add_argument(
        "--workloads",
        default="simple:1,wide:100,wide:500,long_chain:100,long_chain:500",
        help="Comma-separated workload list in flavor:complexity format.",
    )
    parser.add_argument(
        "--scenarios",
        default="ironflow_inproc,ironflow_backend,ironflow_backend_ui,prefect_inproc,prefect_server,prefect_server_ui",
        help="Comma-separated scenario subset.",
    )
    parser.add_argument(
        "--out-json",
        default=str(ROOT / "docs" / "perf_matrix_results.json"),
        help="Path to write raw sample output.",
    )
    parser.add_argument(
        "--out-md",
        default=str(ROOT / "docs" / "perf_matrix_summary.md"),
        help="Path to write markdown summary.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workloads: list[tuple[str, int]] = []
    for token in args.workloads.split(","):
        flavor, complexity = token.split(":")
        workloads.append((flavor.strip(), int(complexity.strip())))

    scenarios = {value.strip() for value in args.scenarios.split(",") if value.strip()}
    samples: list[PerfSample] = []
    next_port = 4300

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["IRONFLOW_HISTORY_PATH"] = str(Path(tmpdir) / "bench_history.jsonl")
        for iteration in range(1, args.iterations + 1):
            for flavor, complexity in workloads:
                if "ironflow_inproc" in scenarios:
                    samples.append(_run_ironflow_inproc(flavor, complexity, iteration))
                if "ironflow_backend" in scenarios:
                    next_port += 2
                    samples.append(
                        _run_ironflow_service_mode(
                            flavor=flavor,
                            complexity=complexity,
                            iteration=iteration,
                            with_ui=False,
                            backend_port=next_port,
                            frontend_port=next_port + 1,
                        )
                    )
                if "ironflow_backend_ui" in scenarios:
                    next_port += 2
                    samples.append(
                        _run_ironflow_service_mode(
                            flavor=flavor,
                            complexity=complexity,
                            iteration=iteration,
                            with_ui=True,
                            backend_port=next_port,
                            frontend_port=next_port + 1,
                        )
                    )
                if "prefect_inproc" in scenarios:
                    samples.append(_run_prefect_inproc(flavor, complexity, iteration))
                if "prefect_server" in scenarios:
                    next_port += 1
                    samples.append(
                        _run_prefect_server_mode(
                            flavor=flavor,
                            complexity=complexity,
                            iteration=iteration,
                            port=next_port,
                            with_ui_probe=False,
                        )
                    )
                if "prefect_server_ui" in scenarios:
                    next_port += 1
                    samples.append(
                        _run_prefect_server_mode(
                            flavor=flavor,
                            complexity=complexity,
                            iteration=iteration,
                            port=next_port,
                            with_ui_probe=True,
                        )
                    )

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps([asdict(sample) for sample in samples], indent=2), encoding="utf-8")

    rows = _aggregate(samples)
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_build_markdown(rows, out_json), encoding="utf-8")

    print(f"Wrote {len(samples)} samples to {out_json}")
    print(f"Wrote markdown summary to {out_md}")


if __name__ == "__main__":
    main()
