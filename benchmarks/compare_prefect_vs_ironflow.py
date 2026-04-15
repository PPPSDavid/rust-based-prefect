from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import httpx

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class PerfResult:
    engine: str
    flavor: str
    complexity: int
    startup_seconds: float
    runtime_seconds: float
    transition_events: int
    transitions_per_second: float
    notes: str = ""


def _run_ironflow(flavor: str, complexity: int) -> PerfResult:
    sys.path.insert(0, str(ROOT / "python-shim" / "src"))
    from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task, wait

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
    def mapped(n: int) -> int:
        first = inc.submit(n)
        mapped_futs = dbl.map(range(n), wait_for=[first])
        wait(mapped_futs)
        return sum(f.result() for f in mapped_futs)

    @flow
    def chained(n: int) -> int:
        f = passthrough.submit(0)
        for i in range(n):
            f = inc.submit(f, wait_for=[f])
        return f.result()

    flow_fn: Callable[[int], int] = mapped if flavor == "mapped" else chained
    t0 = time.perf_counter()
    _ = flow_fn(complexity)
    runtime = time.perf_counter() - t0
    events = len(plane.events())

    return PerfResult(
        engine="ironflow",
        flavor=flavor,
        complexity=complexity,
        startup_seconds=startup,
        runtime_seconds=runtime,
        transition_events=events,
        transitions_per_second=(events / runtime) if runtime > 0 else 0.0,
    )


def _run_prefect(flavor: str, complexity: int, port: int = 4201) -> PerfResult:
    try:
        from prefect import flow, task  # type: ignore
    except Exception as exc:
        return PerfResult(
            engine="prefect",
            flavor=flavor,
            complexity=complexity,
            startup_seconds=0.0,
            runtime_seconds=0.0,
            transition_events=0,
            transitions_per_second=0.0,
            notes=f"prefect import unavailable: {exc}",
        )

    api_url = f"http://127.0.0.1:{port}/api"
    env = os.environ.copy()
    env["PREFECT_API_URL"] = api_url

    server_cmd = ["prefect", "server", "start", "--host", "127.0.0.1", "--port", str(port)]
    startup_begin = time.perf_counter()
    try:
        server = subprocess.Popen(
            server_cmd,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return PerfResult(
            engine="prefect",
            flavor=flavor,
            complexity=complexity,
            startup_seconds=0.0,
            runtime_seconds=0.0,
            transition_events=0,
            transitions_per_second=0.0,
            notes=f"prefect server command unavailable: {exc}",
        )

    startup = 0.0
    try:
        _wait_for_server(port)
        startup = time.perf_counter() - startup_begin

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
        def mapped(n: int) -> int:
            first = inc.submit(n)
            mapped_futs = dbl.map(range(n), wait_for=[first])
            return sum(f.result() for f in mapped_futs)

        @flow
        def chained(n: int) -> int:
            f = passthrough.submit(0)
            for _ in range(n):
                f = inc.submit(f, wait_for=[f])
            return f.result()

        flow_fn: Callable[[int], int] = mapped if flavor == "mapped" else chained
        t0 = time.perf_counter()
        _ = flow_fn(complexity)
        runtime = time.perf_counter() - t0
        transitions = _estimate_transitions(flavor, complexity)

        return PerfResult(
            engine="prefect",
            flavor=flavor,
            complexity=complexity,
            startup_seconds=startup,
            runtime_seconds=runtime,
            transition_events=transitions,
            transitions_per_second=(transitions / runtime) if runtime > 0 else 0.0,
        )
    except Exception as exc:
        return PerfResult(
            engine="prefect",
            flavor=flavor,
            complexity=complexity,
            startup_seconds=startup,
            runtime_seconds=0.0,
            transition_events=0,
            transitions_per_second=0.0,
            notes=f"prefect benchmark failed: {exc}",
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def _run_ironflow_http(flavor: str, complexity: int, port: int = 4210) -> PerfResult:
    env = os.environ.copy()
    python_path_parts = [str(ROOT / "python-shim" / "src")]
    if env.get("PYTHONPATH"):
        python_path_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
    with tempfile.TemporaryDirectory() as tmpdir:
        env["IRONFLOW_HISTORY_PATH"] = str(Path(tmpdir) / "history.jsonl")
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "prefect_compat.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        start = time.perf_counter()
        try:
            server = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            return PerfResult(
                engine="ironflow_http",
                flavor=flavor,
                complexity=complexity,
                startup_seconds=0.0,
                runtime_seconds=0.0,
                transition_events=0,
                transitions_per_second=0.0,
                notes=f"ironflow http server unavailable: {exc}",
            )
        startup = 0.0
        try:
            _wait_for_server(port, [f"http://127.0.0.1:{port}/health"])
            startup = time.perf_counter() - start
            with httpx.Client(timeout=60.0) as client:
                before = client.get(f"http://127.0.0.1:{port}/history/summary").json().get("events", 0)
                response = client.post(
                    f"http://127.0.0.1:{port}/benchmark/run",
                    json={"flavor": flavor, "complexity": complexity},
                )
                response.raise_for_status()
                body = response.json()
                after = client.get(f"http://127.0.0.1:{port}/history/summary").json().get("events", 0)
            delta_events = max(after - before, 0)
            runtime = float(body["runtime_seconds"])
            return PerfResult(
                engine="ironflow_http",
                flavor=flavor,
                complexity=complexity,
                startup_seconds=startup,
                runtime_seconds=runtime,
                transition_events=delta_events,
                transitions_per_second=(delta_events / runtime) if runtime > 0 else 0.0,
            )
        except Exception as exc:
            return PerfResult(
                engine="ironflow_http",
                flavor=flavor,
                complexity=complexity,
                startup_seconds=startup,
                runtime_seconds=0.0,
                transition_events=0,
                transitions_per_second=0.0,
                notes=f"ironflow http benchmark failed: {exc}",
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


def _wait_for_server(port: int, urls: list[str] | None = None, timeout_s: int = 45) -> None:
    deadline = time.time() + timeout_s
    check_urls = urls or [f"http://127.0.0.1:{port}/api/health", f"http://127.0.0.1:{port}/health"]
    while time.time() < deadline:
        for url in check_urls:
            try:
                with httpx.Client(timeout=1.0) as client:
                    r = client.get(url)
                    if r.status_code == 200:
                        return
            except Exception:
                pass
        time.sleep(1)
    raise TimeoutError("Prefect server did not become healthy in time")


def _estimate_transitions(flavor: str, complexity: int) -> int:
    # Rough cross-engine comparable estimate for run + task lifecycle transitions.
    if flavor == "mapped":
        task_count = complexity + 1
    else:
        task_count = complexity + 1
    return 3 + (task_count * 3)


def main() -> None:
    results: list[PerfResult] = []
    for flavor, complexity in [("mapped", 100), ("mapped", 500), ("chained", 100), ("chained", 500)]:
        results.append(_run_ironflow(flavor, complexity))
        results.append(_run_ironflow_http(flavor, complexity))
        results.append(_run_prefect(flavor, complexity))

    payload = [asdict(r) for r in results]
    out = ROOT / "docs" / "perf_comparison.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
