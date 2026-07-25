from __future__ import annotations

import base64
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _wait_for_health(base_url: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.25)
    raise TimeoutError(f"server did not become healthy at {base_url}/health")


def _auth_header(value: str) -> dict[str, str]:
    token = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_uvicorn_server_with_basic_auth(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    shim_src = repo_root / "python-shim" / "src"
    history = tmp_path / "subprocess-history.jsonl"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(shim_src), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    env["FLOWOXIDE_HISTORY_PATH"] = str(history)
    env["FLOWOXIDE_SERVER_API_AUTH_STRING"] = "admin:pass"
    env["FLOWOXIDE_ENABLE_LOCAL_WORKER"] = "0"
    env["FLOWOXIDE_ENABLE_SCHEDULER"] = "0"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "prefect_compat.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
        ],
        env=env,
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = "http://127.0.0.1:8765"
    try:
        _wait_for_health(base_url)
        with urlopen(f"{base_url}/health") as response:
            assert response.status == 200

        try:
            urlopen(f"{base_url}/api/deployments", timeout=3)
            raise AssertionError("expected unauthorized response")
        except HTTPError as exc:
            assert exc.code == 401

        request = Request(
            f"{base_url}/api/deployments",
            headers=_auth_header("admin:pass"),
            method="GET",
        )
        with urlopen(request, timeout=3) as response:
            assert response.status == 200
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=5)
            raise TimeoutError(out or "uvicorn did not exit")
