from __future__ import annotations

import math

import pytest

from prefect_compat.result_codec import (
    ResultEncodeError,
    decode_task_result,
    encode_task_result,
)


def test_encode_roundtrip_json_safe_scalars_and_containers() -> None:
    samples = [
        None,
        True,
        False,
        0,
        42,
        -3,
        1.5,
        "hello",
        [],
        [1, "a", None, {"k": True}],
        {"a": 1, "b": [2, 3], "c": {"d": "e"}},
    ]
    for value in samples:
        raw = encode_task_result(value)
        assert decode_task_result(raw) == value


def test_encode_rejects_non_json_safe_types() -> None:
    class Box:
        pass

    with pytest.raises(ResultEncodeError):
        encode_task_result(Box())
    with pytest.raises(ResultEncodeError):
        encode_task_result(b"bytes")
    with pytest.raises(ResultEncodeError):
        encode_task_result({1: "bad-key"})
    with pytest.raises(ResultEncodeError):
        encode_task_result(math.nan)
    with pytest.raises(ResultEncodeError):
        encode_task_result((1, 2))


def test_encode_rejects_oversized_payload() -> None:
    huge = "x" * (65 * 1024)
    with pytest.raises(ResultEncodeError):
        encode_task_result(huge)


def test_fingerprint_stable_and_sensitive() -> None:
    from prefect_compat.result_codec import fingerprint_task_inputs

    a = fingerprint_task_inputs([1, {"x": 2}], {"b": 3})
    b = fingerprint_task_inputs([1, {"x": 2}], {"b": 3})
    c = fingerprint_task_inputs([1, {"x": 9}], {"b": 3})
    assert a is not None and a == b
    assert a != c


def test_fingerprint_rejects_non_json() -> None:
    from prefect_compat.result_codec import fingerprint_task_inputs

    assert fingerprint_task_inputs([object()], {}) is None
