"""IronFlow-specific exception types for the prefect_compat runtime."""

from __future__ import annotations

from typing import Any


class FlowChildrenFailed(RuntimeError):
    """Raised when ``final_state="wait_all"`` aggregation yields a non-success terminal."""

    def __init__(
        self,
        message: str,
        *,
        flow_run_id: str | None = None,
        resolved_state: str | None = None,
        kind: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.flow_run_id = flow_run_id
        self.resolved_state = resolved_state
        self.kind = kind
        self.details = details or {}


class FlowCatalogConflict(ValueError):
    """Raised when rename/archive/delete is blocked (HTTP 409)."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        deployments: list[dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.deployments = deployments or []
        self.extra = extra or {}

    def http_detail(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": str(self),
            "deployments": self.deployments,
        }
        payload.update(self.extra)
        return payload


class TransitionRewriteFailed(RuntimeError):
    """Raised when a rewrite handler demotes a successful terminal to FAILED."""

    def __init__(
        self,
        message: str,
        *,
        committed: str | None = None,
        proposed: str | None = None,
    ) -> None:
        super().__init__(message)
        self.committed = committed
        self.proposed = proposed
