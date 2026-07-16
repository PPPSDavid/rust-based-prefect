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
