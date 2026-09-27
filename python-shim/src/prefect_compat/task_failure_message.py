"""Copy a task exception onto event payloads, task rows, and log lines."""

from __future__ import annotations

import json
import traceback
from typing import Any


def exception_failure_details(exc: BaseException) -> dict[str, str]:
    """Exception text, plus a traceback when the exception still has a stack."""
    details = {"error": str(exc)}
    if exc.__traceback__ is None:
        return details
    formatted = "".join(traceback.format_exception(exc)).strip()
    if formatted:
        details["traceback"] = formatted
    return details


def merge_failure_details(
    data: dict[str, Any] | None,
    exc: BaseException | None,
    *,
    failed: bool,
) -> dict[str, Any] | None:
    """Add error and traceback when a task terminal commit stays FAILED."""
    if not failed or exc is None:
        return data
    merged = dict(data or {})
    for key, value in exception_failure_details(exc).items():
        merged.setdefault(key, value)
    return merged


def task_event_log_message(
    task_name: str, event_type: str, data: dict[str, Any] | None
) -> str:
    """Transition log line. Failures append the exception and traceback."""
    message = f"{task_name}: {event_type}"
    if event_type != "task_failed" or not isinstance(data, dict):
        return message
    error = data.get("error")
    if isinstance(error, str) and error:
        message = f"{message}: {error}"
    stack = data.get("traceback")
    if isinstance(stack, str) and stack:
        message = f"{message}\n{stack}"
    return message


def failure_fields_from_payload(data: Any) -> tuple[str | None, str | None]:
    """Read ``error`` and ``traceback`` from a task_failed event payload."""
    if isinstance(data, str):
        try:
            data = json.loads(data or "{}")
        except json.JSONDecodeError:
            return None, None
    if not isinstance(data, dict):
        return None, None
    error = data.get("error")
    stack = data.get("traceback")
    error_text = error if isinstance(error, str) and error else None
    stack_text = stack if isinstance(stack, str) and stack else None
    return error_text, stack_text
