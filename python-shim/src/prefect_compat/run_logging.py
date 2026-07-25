"""Prefect-shaped ``get_run_logger`` → control-plane log rows (P3.1).

User log emission must not hold the control-plane FSM lock; ``append_log``
performs a store insert only.
"""

from __future__ import annotations

import logging
import sys

_LOGGER_CACHE: dict[str, logging.Logger] = {}


class _ControlPlaneLogHandler(logging.Handler):
    """Append records to the active control plane when a flow run is bound."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return

        # Circular: handlers must resolve the live plane/ContextVars at emit time.
        from .context import _ACTIVE_TASK_RUN_ID
        from .decorators import _ACTIVE_FLOW_RUN, _CONTROL_PLANE

        flow_run_id = _ACTIVE_FLOW_RUN.get()
        if flow_run_id is None:
            # Outside a run: best-effort stderr so library code does not crash.
            try:
                sys.stderr.write(f"{record.levelname}: {message}\n")
            except Exception:
                pass
            return

        task_run_id = _ACTIVE_TASK_RUN_ID.get()
        try:
            _CONTROL_PLANE.append_log(
                flow_run_id=flow_run_id,
                message=message,
                level=record.levelname,
                task_run_id=task_run_id,
            )
        except Exception:
            self.handleError(record)


def get_run_logger(name: str | None = None) -> logging.Logger:
    """Return a stdlib logger that writes to the flow-run log store.

    Inside an active ``@flow`` / ``@task`` body, messages become rows visible via
    ``GET /api/flow-runs/{id}/logs`` and the UI Logs tab.

    Outside a run, messages go to stderr and are not persisted.

    Note: process-isolated task workers do not inherit ContextVars; log from the
    parent/orchestrating thread or use sequential/thread runners for task-scoped
    logger association.
    """
    logger_name = name or "ironflow.run"
    cached = _LOGGER_CACHE.get(logger_name)
    if cached is not None:
        return cached

    logger = logging.getLogger(logger_name)
    # Avoid duplicate handlers if the process reloads the module.
    if not any(isinstance(h, _ControlPlaneLogHandler) for h in logger.handlers):
        handler = _ControlPlaneLogHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    _LOGGER_CACHE[logger_name] = logger
    return logger
