from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from typing import Any


def _candidate_lib_paths() -> list[Path]:
    env = os.getenv("IRONFLOW_RUST_LIB")
    if env:
        return [Path(env)]
    repo_root = Path(__file__).resolve().parents[3]
    return [
        repo_root / "rust-engine" / "target" / "release" / "ironflow_engine.dll",
        repo_root / "rust-engine" / "target" / "debug" / "ironflow_engine.dll",
        repo_root / "rust-engine" / "target" / "release" / "libironflow_engine.so",
        repo_root / "rust-engine" / "target" / "debug" / "libironflow_engine.so",
        repo_root / "rust-engine" / "target" / "release" / "libironflow_engine.dylib",
        repo_root / "rust-engine" / "target" / "debug" / "libironflow_engine.dylib",
    ]


def native_library_available() -> bool:
    """True when a prebuilt ``ironflow_engine`` cdylib exists on disk (release or debug)."""
    return any(p.exists() for p in _candidate_lib_paths())


_ironflow_lib: ctypes.CDLL | None = None


def load_ironflow_library() -> ctypes.CDLL:
    """Load the ironflow-engine cdylib once (shared by UI query bridge and FSM control)."""
    global _ironflow_lib
    if _ironflow_lib is not None:
        return _ironflow_lib
    for path in _candidate_lib_paths():
        if path.exists():
            _ironflow_lib = ctypes.CDLL(str(path))
            _configure_ironflow_symbols(_ironflow_lib)
            return _ironflow_lib
    raise RuntimeError("Rust ironflow_engine library not found. Build rust-engine first.")


def _configure_ironflow_symbols(lib: ctypes.CDLL) -> None:
    lib.ironflow_query.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
    lib.ironflow_query.restype = ctypes.c_void_p

    lib.ironflow_free_string.argtypes = [ctypes.c_void_p]
    lib.ironflow_free_string.restype = None

    lib.ironflow_engine_new.argtypes = []
    lib.ironflow_engine_new.restype = ctypes.c_uint64

    lib.ironflow_engine_free.argtypes = [ctypes.c_uint64]
    lib.ironflow_engine_free.restype = None

    lib.ironflow_control.argtypes = [ctypes.c_uint64, ctypes.c_char_p, ctypes.c_char_p]
    lib.ironflow_control.restype = ctypes.c_void_p


def _decode_json_ptr(lib: ctypes.CDLL, raw_ptr: int) -> Any:
    if not raw_ptr:
        raise RuntimeError("Rust FFI returned null pointer")
    try:
        payload = ctypes.string_at(raw_ptr).decode("utf-8")
    finally:
        lib.ironflow_free_string(raw_ptr)
    return json.loads(payload)


class RustQueryBridge:
    def __init__(self) -> None:
        self._lib = load_ironflow_library()

    def query(self, db_path: str, kind: str, params: dict[str, Any]) -> Any:
        raw_ptr = self._lib.ironflow_query(
            db_path.encode("utf-8"),
            kind.encode("utf-8"),
            json.dumps(params).encode("utf-8"),
        )
        parsed = _decode_json_ptr(self._lib, raw_ptr)
        if isinstance(parsed, dict) and "error" in parsed:
            raise RuntimeError(str(parsed["error"]))
        return parsed


class RustFsmBridge:
    """Thread-safe native FSM (``Engine``) behind ``ironflow_engine_new`` / ``ironflow_control``."""

    def __init__(self) -> None:
        self._lib = load_ironflow_library()

    def engine_new(self) -> int:
        h = int(self._lib.ironflow_engine_new())
        if h == 0:
            raise RuntimeError("ironflow_engine_new returned invalid handle 0")
        return h

    def engine_free(self, handle: int) -> None:
        if handle:
            self._lib.ironflow_engine_free(ctypes.c_uint64(handle))

    def control(self, handle: int, op: str, body: dict[str, Any]) -> dict[str, Any]:
        raw_ptr = self._lib.ironflow_control(
            ctypes.c_uint64(handle),
            op.encode("utf-8"),
            json.dumps(body).encode("utf-8"),
        )
        out = _decode_json_ptr(self._lib, raw_ptr)
        if not isinstance(out, dict):
            raise RuntimeError("unexpected Rust control response")
        return out
