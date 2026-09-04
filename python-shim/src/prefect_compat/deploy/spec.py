from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def parse_entrypoint(raw: str) -> tuple[str, str]:
    """Convert ``flows/etl.py:my_etl`` to ``("flows.etl", "my_etl")``."""
    module_part, func = raw.rsplit(":", 1)
    if "/" in module_part or module_part.endswith(".py"):
        module = module_part.replace("/", ".").removesuffix(".py")
    else:
        module = module_part
    return module, func


class ScheduleSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cron: str | None = None
    interval_seconds: int | None = Field(default=None, alias="interval")
    rrule: str | None = None
    enabled: bool | None = None


class WorkPoolRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str


class PullStepSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    step: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class DeploymentSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    flow_name: str | None = None
    entrypoint: str | None = None
    path: str | None = None
    default_parameters: dict[str, Any] = Field(default_factory=dict)
    work_pool_name: str | None = None
    paused: bool = False
    concurrency_limit: int | None = None
    collision_strategy: str = "ENQUEUE"
    schedule_interval_seconds: int | None = None
    schedule_cron: str | None = None
    schedule_rrule: str | None = None
    schedule_enabled: bool = False
    formerly: list[str] = Field(default_factory=list)

    @classmethod
    def from_entrypoint(
        cls,
        *,
        name: str,
        entrypoint: str,
        parameters: dict[str, Any] | None = None,
        work_pool_name: str | None = None,
        schedule_cron: str | None = None,
        schedule_interval_seconds: int | None = None,
        schedule_rrule: str | None = None,
        **kwargs: Any,
    ) -> DeploymentSpec:
        module, func = parse_entrypoint(entrypoint)
        schedule_enabled = bool(
            (schedule_cron and schedule_cron.strip())
            or (schedule_interval_seconds and schedule_interval_seconds > 0)
            or (schedule_rrule and schedule_rrule.strip())
        )
        return cls(
            name=name,
            flow_name=func,
            entrypoint=f"{module}:{func}",
            default_parameters=parameters or {},
            work_pool_name=work_pool_name,
            schedule_cron=schedule_cron,
            schedule_interval_seconds=schedule_interval_seconds,
            schedule_rrule=schedule_rrule,
            schedule_enabled=schedule_enabled,
            **kwargs,
        )

    def to_api_body(self, work_pool_id: str) -> dict[str, Any]:
        return {
            "name": self.name,
            "flow_name": self.flow_name,
            "entrypoint": self.entrypoint,
            "path": self.path,
            "default_parameters": self.default_parameters,
            "paused": self.paused,
            "concurrency_limit": self.concurrency_limit,
            "collision_strategy": self.collision_strategy,
            "schedule_interval_seconds": self.schedule_interval_seconds,
            "schedule_cron": self.schedule_cron,
            "schedule_rrule": self.schedule_rrule,
            "schedule_enabled": self.schedule_enabled,
            "work_pool_id": work_pool_id,
            "formerly": list(self.formerly),
        }


def _normalize_deployment(raw: Any) -> DeploymentSpec:
    if isinstance(raw, DeploymentSpec):
        return raw
    if not isinstance(raw, dict):
        raise TypeError(f"deployment must be a mapping, got {type(raw)!r}")

    data = dict(raw)

    if "parameters" in data:
        data["default_parameters"] = data.pop("parameters")

    work_pool = data.pop("work_pool", None)
    if isinstance(work_pool, dict):
        data["work_pool_name"] = work_pool.get("name")
    elif isinstance(work_pool, WorkPoolRef):
        data["work_pool_name"] = work_pool.name

    schedule = data.pop("schedule", None)
    if isinstance(schedule, dict):
        sched = ScheduleSpec.model_validate(schedule)
        if sched.cron:
            data["schedule_cron"] = sched.cron
        if sched.interval_seconds is not None:
            data["schedule_interval_seconds"] = sched.interval_seconds
        if sched.rrule:
            data["schedule_rrule"] = sched.rrule
        if sched.enabled is not None:
            data["schedule_enabled"] = sched.enabled
        elif sched.cron or sched.interval_seconds or sched.rrule:
            data["schedule_enabled"] = True
    elif isinstance(schedule, ScheduleSpec):
        if schedule.cron:
            data["schedule_cron"] = schedule.cron
        if schedule.interval_seconds is not None:
            data["schedule_interval_seconds"] = schedule.interval_seconds
        if schedule.rrule:
            data["schedule_rrule"] = schedule.rrule
        if schedule.enabled is not None:
            data["schedule_enabled"] = schedule.enabled
        elif schedule.cron or schedule.interval_seconds or schedule.rrule:
            data["schedule_enabled"] = True

    entrypoint = data.get("entrypoint")
    if isinstance(entrypoint, str) and ":" in entrypoint:
        module, func = parse_entrypoint(entrypoint)
        data.setdefault("flow_name", func)
        data["entrypoint"] = f"{module}:{func}"

    return DeploymentSpec.model_validate(data)


class IronflowManifest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    ironflow_version: str = Field(alias="ironflow-version")
    deployments: list[DeploymentSpec] = Field(default_factory=list)

    @field_validator("deployments", mode="before")
    @classmethod
    def _normalize_deployments(cls, value: Any) -> list[DeploymentSpec]:
        if not value:
            return []
        return [_normalize_deployment(item) for item in value]
