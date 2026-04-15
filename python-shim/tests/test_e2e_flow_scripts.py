from __future__ import annotations

import os
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ironflow_script_e2e():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "python-shim" / "src")
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
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert "prefect_result=26" in proc.stdout
