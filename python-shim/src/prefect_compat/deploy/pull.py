from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .spec import PullStepSpec


def set_working_directory(directory: str) -> dict[str, str]:
    """Set the process working directory before importing flow code."""
    os.chdir(directory)
    return {"directory": os.getcwd()}


STEP_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "ironflow.deployments.steps.set_working_directory": set_working_directory,
}


def run_pull_steps(steps: list[PullStepSpec]) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    for spec in steps:
        step_fn = STEP_REGISTRY.get(spec.step)
        if step_fn is None:
            raise ValueError(f"unknown pull step: {spec.step}")
        result = step_fn(**spec.inputs)
        if not isinstance(result, dict):
            raise TypeError(
                f"pull step {spec.step!r} must return a dict, got {type(result)!r}"
            )
        outputs.update(result)
    return outputs
