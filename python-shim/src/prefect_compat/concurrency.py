"""Prefect-compatible global concurrency and rate-limit helpers.

Uses the control-plane slot ledger (Rust-preferred SQLite). Tag-based task
limits reuse the same ledger under names ``tag:{tag}`` — see
``docs/plans/concurrency-limits.md`` and ``docs/how-to/concurrency-limits.md``.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from .context import MissingContextError, get_run_context
from .runtime import InMemoryControlPlane

_LOG = logging.getLogger(__name__)

DEFAULT_LEASE_DURATION = 300.0
DEFAULT_TAG_WAIT_SECONDS = float(
    os.getenv("IRONFLOW_TASK_TAG_SLOT_WAIT_SECONDS", "1.0")
)


class ConcurrencySlotTimeoutError(TimeoutError):
    """Raised when slots cannot be acquired before ``timeout_seconds``."""


class ConcurrencyLimitError(RuntimeError):
    """Raised for strict missing limits, decay-required, or tag deny (limit 0)."""


def _plane() -> InMemoryControlPlane:
    from .decorators import _require_control_plane

    return _require_control_plane()


def _normalize_names(names: str | Sequence[str]) -> list[str]:
    if isinstance(names, str):
        return [names]
    out = [str(n) for n in names]
    if not out:
        raise ValueError("names must be non-empty")
    return out


def create_concurrency_limit(
    name: str,
    limit: int,
    *,
    slot_decay_per_second: float | None = None,
    active: bool = True,
    plane: InMemoryControlPlane | None = None,
) -> dict[str, Any]:
    """Create or update a named global concurrency limit."""
    cp = plane or _plane()
    return cp.upsert_concurrency_limit(
        name=name,
        limit=limit,
        slot_decay_per_second=slot_decay_per_second,
        active=active,
    )


def delete_concurrency_limit(
    name: str, *, plane: InMemoryControlPlane | None = None
) -> dict[str, Any]:
    cp = plane or _plane()
    return cp.delete_concurrency_limit(name)


def get_concurrency_limit(
    name: str, *, plane: InMemoryControlPlane | None = None
) -> dict[str, Any] | None:
    cp = plane or _plane()
    return cp.get_concurrency_limit(name)


def list_concurrency_limits(
    *, plane: InMemoryControlPlane | None = None
) -> list[dict[str, Any]]:
    cp = plane or _plane()
    return cp.list_concurrency_limits()


def create_tag_concurrency_limit(
    tag: str,
    limit: int,
    *,
    active: bool = True,
    plane: InMemoryControlPlane | None = None,
) -> dict[str, Any]:
    """Create/update a tag-based limit stored as ``tag:{tag}``."""
    return create_concurrency_limit(f"tag:{tag}", limit, active=active, plane=plane)


def _acquire_blocking(
    cp: InMemoryControlPlane,
    names: list[str],
    *,
    occupy: int,
    mode: str,
    timeout_seconds: float | None,
    strict: bool,
    lease_duration: float,
    holder_type: str | None,
    holder_id: str | None,
    poll_seconds: float,
) -> dict[str, Any]:
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    while True:
        out = cp.acquire_concurrency_slots(
            names,
            occupy=occupy,
            mode=mode,
            strict=strict,
            lease_duration=lease_duration,
            holder_type=holder_type,
            holder_id=holder_id,
        )
        status = out.get("status")
        if status in ("acquired", "bypassed"):
            return out
        if status == "denied":
            raise ConcurrencyLimitError(
                f"concurrency limit denied (limit=0) for {out.get('name')!r}"
            )
        if status == "missing":
            raise ConcurrencyLimitError(
                f"concurrency limit missing: {out.get('error', {}).get('name')}"
            )
        if status == "inactive":
            raise ConcurrencyLimitError(
                f"concurrency limit inactive: {out.get('error', {}).get('name')}"
            )
        if status == "decay_required":
            raise ConcurrencyLimitError(
                out.get("error", {}).get("message", "slot_decay_per_second required")
            )
        if status != "would_block":
            if out.get("ok") is False:
                raise ConcurrencyLimitError(str(out.get("error", out)))
            raise ConcurrencyLimitError(f"unexpected acquire status: {status}")
        if deadline is not None and time.monotonic() >= deadline:
            raise ConcurrencySlotTimeoutError(
                f"timed out acquiring concurrency slots for {names}"
            )
        time.sleep(poll_seconds)


@contextmanager
def concurrency(
    names: str | Sequence[str],
    occupy: int = 1,
    *,
    timeout_seconds: float | None = None,
    strict: bool = False,
    lease_duration: float = DEFAULT_LEASE_DURATION,
    holder_type: str | None = None,
    holder_id: str | None = None,
    poll_seconds: float = 0.05,
    plane: InMemoryControlPlane | None = None,
) -> Iterator[list[str]]:
    """Acquire slots for the duration of the block; release on exit.

    If a named limit does not exist (or is inactive) and ``strict`` is False,
    acquisition is a no-op (Prefect-compatible soft mode).
    """
    cp = plane or _plane()
    name_list = _normalize_names(names)
    bound_holder_type = holder_type
    bound_holder_id = holder_id
    if bound_holder_id is None:
        try:
            ctx = get_run_context()
        except MissingContextError:
            ctx = None
        if ctx is not None:
            if ctx.task_run_id is not None:
                bound_holder_type = bound_holder_type or "task_run"
                bound_holder_id = str(ctx.task_run_id)
            elif ctx.flow_run_id is not None:
                bound_holder_type = bound_holder_type or "flow_run"
                bound_holder_id = str(ctx.flow_run_id)
    out = _acquire_blocking(
        cp,
        name_list,
        occupy=occupy,
        mode="concurrency",
        timeout_seconds=timeout_seconds,
        strict=strict,
        lease_duration=lease_duration,
        holder_type=bound_holder_type,
        holder_id=bound_holder_id,
        poll_seconds=poll_seconds,
    )
    lease_ids = [str(x) for x in out.get("lease_ids") or []]
    if out.get("status") == "bypassed":
        _LOG.warning(
            "concurrency limits %s missing or inactive; proceeding without slots "
            "(strict=False)",
            name_list,
        )
    try:
        yield lease_ids
    finally:
        if lease_ids:
            cp.release_concurrency_slots(lease_ids)


def rate_limit(
    names: str | Sequence[str],
    occupy: int = 1,
    *,
    timeout_seconds: float | None = None,
    strict: bool = False,
    poll_seconds: float = 0.05,
    plane: InMemoryControlPlane | None = None,
) -> None:
    """Block until slots are acquired under rate-limit mode (requires slot decay).

    Slots auto-expire via ``slot_decay_per_second``; no explicit release is needed.
    """
    cp = plane or _plane()
    name_list = _normalize_names(names)
    _acquire_blocking(
        cp,
        name_list,
        occupy=occupy,
        mode="rate_limit",
        timeout_seconds=timeout_seconds,
        strict=strict,
        lease_duration=DEFAULT_LEASE_DURATION,
        holder_type=None,
        holder_id=None,
        poll_seconds=poll_seconds,
    )


def acquire_tag_slots_for_task(
    tags: Sequence[str],
    *,
    task_run_id: str,
    timeout_seconds: float | None = None,
    poll_seconds: float | None = None,
    plane: InMemoryControlPlane | None = None,
) -> list[str]:
    """Acquire ``tag:{t}`` slots for all tags (AND). Empty tags → no-op.

    Raises ``ConcurrencyLimitError`` when any configured tag limit is 0 (deny).
    Unlimited / missing tag limits do not block (soft mode).
    """
    if not tags:
        return []
    cp = plane or _plane()
    names = [f"tag:{t}" for t in tags]
    wait = DEFAULT_TAG_WAIT_SECONDS if poll_seconds is None else float(poll_seconds)
    out = _acquire_blocking(
        cp,
        names,
        occupy=1,
        mode="concurrency",
        timeout_seconds=timeout_seconds,
        strict=False,
        lease_duration=DEFAULT_LEASE_DURATION,
        holder_type="task_run",
        holder_id=task_run_id,
        poll_seconds=wait,
    )
    return [str(x) for x in out.get("lease_ids") or []]
