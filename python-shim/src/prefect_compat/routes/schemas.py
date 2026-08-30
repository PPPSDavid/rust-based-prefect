"""Shared Pydantic models for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BenchmarkRequest(BaseModel):
    flavor: str
    complexity: int


class CursorPage(BaseModel):
    items: list[dict]
    next_cursor: str | None = None


class DeploymentCreateRequest(BaseModel):
    name: str
    flow_name: str
    entrypoint: str | None = None
    path: str | None = None
    default_parameters: dict = Field(default_factory=dict)
    paused: bool = False
    concurrency_limit: int | None = None
    collision_strategy: str = "ENQUEUE"
    schedule_interval_seconds: int | None = None
    schedule_cron: str | None = None
    schedule_rrule: str | None = None
    schedule_next_run_at: str | None = None
    schedule_enabled: bool = False
    work_pool_id: str | None = None


class DeploymentPatchRequest(BaseModel):
    entrypoint: str | None = None
    path: str | None = None
    default_parameters: dict[str, object] | None = None
    paused: bool | None = None
    concurrency_limit: int | None = None
    collision_strategy: str | None = None
    schedule_interval_seconds: int | None = None
    schedule_cron: str | None = None
    schedule_rrule: str | None = None
    schedule_next_run_at: str | None = None
    schedule_enabled: bool | None = None
    work_pool_id: str | None = None


class WorkPoolCreateRequest(BaseModel):
    name: str
    type: str = "process"


class WorkPoolPatchRequest(BaseModel):
    paused: bool | None = None


class ConcurrencyLimitCreateRequest(BaseModel):
    name: str
    limit: int = Field(ge=0)
    slot_decay_per_second: float | None = Field(default=None, gt=0)
    active: bool = True


class ConcurrencyLimitPatchRequest(BaseModel):
    limit: int | None = Field(default=None, ge=0)
    slot_decay_per_second: float | None = Field(default=None, gt=0)
    active: bool | None = None


class DeploymentRunTriggerRequest(BaseModel):
    parameters: dict | None = None
    idempotency_key: str | None = None


class FlowRunPauseRequest(BaseModel):
    """Operator pause — ``mode`` is required (``drain`` or ``terminate``)."""

    mode: str


class FlowRunCancelRequest(BaseModel):
    """Cancel always terminates; optional body may only send ``mode=terminate``."""

    mode: str | None = None
