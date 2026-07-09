from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from ..worker import run_worker_loop
from .client import DeployClient
from .pull import run_pull_steps
from .spec import DeploymentSpec, PullStepSpec


def _resolve_flow_fn(flow: Callable[..., Any]) -> Callable[..., Any]:
    return getattr(flow, "fn", flow)


def _entrypoint_from_flow(flow: Callable[..., Any]) -> str:
    fn = _resolve_flow_fn(flow)
    return f"{fn.__module__}:{fn.__name__}"


def deploy(
    flow: Callable[..., Any] | None = None,
    *,
    name: str,
    entrypoint: str | None = None,
    parameters: dict[str, Any] | None = None,
    work_pool_name: str = "default-process-pool",
    api_url: str = "http://127.0.0.1:8000",
    schedule_cron: str | None = None,
    schedule_interval_seconds: int | None = None,
    schedule_rrule: str | None = None,
    dry_run: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    session = kwargs.pop("session", None)
    if entrypoint is None:
        if flow is None:
            raise ValueError("entrypoint is required when flow is not provided")
        entrypoint = _entrypoint_from_flow(flow)

    spec = DeploymentSpec.from_entrypoint(
        name=name,
        entrypoint=entrypoint,
        parameters=parameters,
        work_pool_name=work_pool_name,
        schedule_cron=schedule_cron,
        schedule_interval_seconds=schedule_interval_seconds,
        schedule_rrule=schedule_rrule,
        **kwargs,
    )
    client = DeployClient(api_url, session=session)
    try:
        return client.upsert_deployment(spec, dry_run=dry_run)
    finally:
        client.close()


def serve(
    flow: Callable[..., Any],
    *,
    name: str,
    pull_steps: list[PullStepSpec] | None = None,
    api_url: str = "http://127.0.0.1:8000",
    work_pool_name: str = "default-process-pool",
    worker_name: str = "serve-worker",
    stop_event: threading.Event | None = None,
    **deploy_kwargs: Any,
) -> None:
    result = deploy(
        flow,
        name=name,
        api_url=api_url,
        work_pool_name=work_pool_name,
        **deploy_kwargs,
    )
    if result.get("dry_run"):
        raise ValueError("serve() cannot be used with dry_run=True")

    run_pull_steps(pull_steps or [])

    fn = _resolve_flow_fn(flow)
    flow_registry = {fn.__name__: flow}

    from ..server import control_plane

    work_pool_id = work_pool_name
    deployment = result.get("deployment")
    if isinstance(deployment, dict) and deployment.get("work_pool_id"):
        work_pool_id = str(deployment["work_pool_id"])

    event = stop_event or threading.Event()
    run_worker_loop(
        control_plane,
        worker_name=worker_name,
        work_pool_id=work_pool_id,
        flow_registry=flow_registry,
        stop_event=event,
    )
