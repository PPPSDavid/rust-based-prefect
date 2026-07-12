"""Narrow ``@task`` decorator results for static type checking in benchmarks."""

from __future__ import annotations

from typing import TypeVar, cast

from prefect_compat.decorators import TaskWrapper

T = TypeVar("T")


def as_task_wrapper(value: T) -> TaskWrapper:
    return cast(TaskWrapper, value)
