"""Public run context helpers (P3.0).

Prefect-shaped access to the active flow/task run ids without reaching into
internal ContextVars. Built on the same ContextVars the decorators maintain.
"""

from __future__ import annotations

import contextvars
import inspect
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

_ACTIVE_FLOW_NAME: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ironflow_active_flow_name", default=None
)
_ACTIVE_FLOW_PARAMETERS: contextvars.ContextVar[Mapping[str, Any] | None] = (
    contextvars.ContextVar("ironflow_active_flow_parameters", default=None)
)
_ACTIVE_TASK_RUN_ID: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "ironflow_active_task_run_id", default=None
)
_ACTIVE_TASK_NAME: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ironflow_active_task_name", default=None
)


class MissingContextError(RuntimeError):
    """Raised when ``get_run_context`` is called outside an active flow run."""


@dataclass(frozen=True)
class RunContext:
    """Snapshot of the active flow (and optional task) run.

    ``parameters`` are the bound keyword arguments of the flow call when known.
    Deployment fields are filled when the flow is executing a claimed
    deployment run; otherwise they are ``None``.
    """

    flow_run_id: UUID
    flow_name: str
    task_run_id: UUID | None = None
    task_name: str | None = None
    deployment_run_id: UUID | None = None
    deployment_id: UUID | None = None
    deployment_name: str | None = None
    parameters: Mapping[str, Any] | None = None


def get_run_context() -> RunContext:
    """Return the active ``RunContext``, or raise ``MissingContextError``."""
    # Circular: decorators own flow-run ContextVars and import bind_* from here.
    from .decorators import _ACTIVE_DEPLOYMENT_RUN, _ACTIVE_FLOW_RUN, _CONTROL_PLANE

    flow_run_id = _ACTIVE_FLOW_RUN.get()
    if flow_run_id is None:
        raise MissingContextError(
            "get_run_context() requires an active @flow invocation"
        )

    flow_name = _ACTIVE_FLOW_NAME.get()
    if not flow_name:
        try:
            flow_name = _CONTROL_PLANE.get_flow(flow_run_id).name
        except Exception:
            flow_name = "<unknown>"

    deployment_run_id = _ACTIVE_DEPLOYMENT_RUN.get()
    deployment_id: UUID | None = None
    deployment_name: str | None = None
    if deployment_run_id is not None:
        try:
            dep_run = _CONTROL_PLANE.get_deployment_run(deployment_run_id)
        except Exception:
            dep_run = None
        if dep_run:
            raw_dep_id = dep_run.get("deployment_id")
            if raw_dep_id:
                deployment_id = UUID(str(raw_dep_id))
                try:
                    dep = _CONTROL_PLANE.get_deployment(deployment_id)
                except Exception:
                    dep = None
                if dep:
                    deployment_name = str(dep.get("name") or "") or None

    return RunContext(
        flow_run_id=flow_run_id,
        flow_name=flow_name,
        task_run_id=_ACTIVE_TASK_RUN_ID.get(),
        task_name=_ACTIVE_TASK_NAME.get(),
        deployment_run_id=deployment_run_id,
        deployment_id=deployment_id,
        deployment_name=deployment_name,
        parameters=_ACTIVE_FLOW_PARAMETERS.get(),
    )


@contextmanager
def bind_flow_metadata(
    flow_name: str,
    parameters: Mapping[str, Any] | None = None,
) -> Iterator[None]:
    """Bind flow name/parameters for the duration of a flow body."""
    name_token = _ACTIVE_FLOW_NAME.set(flow_name)
    params_token = _ACTIVE_FLOW_PARAMETERS.set(parameters)
    try:
        yield
    finally:
        _ACTIVE_FLOW_PARAMETERS.reset(params_token)
        _ACTIVE_FLOW_NAME.reset(name_token)


@contextmanager
def bind_task_run(task_run_id: UUID, task_name: str) -> Iterator[None]:
    """Bind task run id/name while a task body executes (in-process)."""
    id_token = _ACTIVE_TASK_RUN_ID.set(task_run_id)
    name_token = _ACTIVE_TASK_NAME.set(task_name)
    try:
        yield
    finally:
        _ACTIVE_TASK_NAME.reset(name_token)
        _ACTIVE_TASK_RUN_ID.reset(id_token)


def bound_flow_parameters(
    fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Best-effort bind of flow call arguments for ``RunContext.parameters``."""
    try:
        sig = inspect.signature(fn)
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)
    except Exception:
        return dict(kwargs)
