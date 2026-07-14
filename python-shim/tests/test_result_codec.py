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
