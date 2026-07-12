from __future__ import annotations

import os
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _is_prefect_temp_server_error(stderr: str) -> bool:
    lowered = stderr.lower()
    return "prefect - starting temporary server" in lowered and (
        "503" in lowered or "service unavailable" in lowered
    )


def test_ironflow_script_e2e(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "python-shim" / "src")
    env["IRONFLOW_HISTORY_PATH"] = str(tmp_path / "script_history.jsonl")
    script = ROOT / "python-shim" / "examples" / "flow_ironflow.py"

    proc = subprocess.run(
        [sys.executable, str(script)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert "ironflow_result=26" in proc.stdout
    assert "ironflow_events=" in proc.stdout


def test_prefect_script_e2e_if_available():
    if find_spec("prefect") is None:
        return

    script = ROOT / "python-shim" / "examples" / "flow_prefect.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if proc.returncode != 0 and _is_prefect_temp_server_error(proc.stderr):
        pytest.skip(msg="Prefect temporary server returned 503 in this environment")  # ty: ignore[unknown-argument]
    assert proc.returncode == 0, proc.stderr
    assert "prefect_result=26" in proc.stdout


def test_detects_prefect_temporary_server_503_error():
    stderr = (
        "INFO prefect - Starting temporary server on http://127.0.0.1:8535\n"
        "HTTPStatusError: 503 Server Error: Service Unavailable\n"
    )
    assert _is_prefect_temp_server_error(stderr)


def test_does_not_misclassify_non_prefect_runtime_errors():
    stderr = "ValueError: bad argument in user flow code"
    assert not _is_prefect_temp_server_error(stderr)
