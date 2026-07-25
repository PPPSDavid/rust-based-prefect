from __future__ import annotations

import os
from pathlib import Path

from prefect_compat.deploy.pull import run_pull_steps
from prefect_compat.deploy.spec import PullStepSpec


def test_run_pull_steps_changes_cwd(tmp_path: Path, monkeypatch) -> None:
    original = Path.cwd()
    target = tmp_path / "workdir"
    target.mkdir()
    monkeypatch.chdir(original)

    result = run_pull_steps(
        [
            PullStepSpec(
                step="flowoxide.deployments.steps.set_working_directory",
                inputs={"directory": str(target)},
            )
        ]
    )

    assert Path.cwd() == target.resolve()
    assert result["directory"] == str(target.resolve())

    os.chdir(original)
