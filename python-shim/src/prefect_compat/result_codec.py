"""JSON-safe task result codec for resume persistence (v1 allowlist)."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

# Keep in sync with docs/plans/task-result-cache.md §5.3
MAX_ENCODED_BYTES = 64 * 1024
MAX_NESTING_DEPTH = 32
MAX_CONTAINER_ELEMENTS = 10_000


class ResultEncodeError(ValueError):
    """Raised when a value cannot be persisted under the v1 allowlist."""


def encode_task_result(value: Any) -> str:
    """Encode a JSON-safe task result to a compact UTF-8 JSON string."""
    _validate(value, depth=0)
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ResultEncodeError(str(exc)) from exc
    if len(raw.encode("utf-8")) > MAX_ENCODED_BYTES:
        raise ResultEncodeError(
            f"result payload exceeds {MAX_ENCODED_BYTES} bytes after encoding"
        )
    return raw


def decode_task_result(raw: str) -> Any:
    """Decode a previously encoded JSON-safe task result."""
    return json.loads(raw)


def fingerprint_task_inputs(args: list[Any] | tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    """Stable SHA-256 of JSON-safe submit/map inputs, or ``None`` if not fingerprintable.

    ``None`` means the call must not resume-skip (cannot prove inputs match).
    """
    try:
        payload = {
            "args": list(args),
            "kwargs": {str(k): kwargs[k] for k in sorted(kwargs.keys(), key=str)},
        }
        raw = encode_task_result(payload)
    except ResultEncodeError:
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fingerprint_parameters(parameters: dict[str, Any] | None) -> str | None:
    """Fingerprint flow/deployment parameters for resume param-guard."""
    return fingerprint_task_inputs((), dict(parameters or {}))


def _validate(value: Any, *, depth: int) -> None:
    if depth > MAX_NESTING_DEPTH:
        raise ResultEncodeError(f"result nesting exceeds depth {MAX_NESTING_DEPTH}")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ResultEncodeError("float result must be finite (no nan/inf)")
        return
    if isinstance(value, str):
        return
    if isinstance(value, list):
        if len(value) > MAX_CONTAINER_ELEMENTS:
            raise ResultEncodeError(
                f"list result exceeds {MAX_CONTAINER_ELEMENTS} elements"
            )
        for item in value:
            _validate(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_CONTAINER_ELEMENTS:
            raise ResultEncodeError(
                f"dict result exceeds {MAX_CONTAINER_ELEMENTS} elements"
            )
        for key, item in value.items():
            if not isinstance(key, str):
                raise ResultEncodeError("dict result keys must be strings")
            _validate(item, depth=depth + 1)
        return
    raise ResultEncodeError(
        f"unsupported result type for persist_result: {type(value).__name__}"
    )
