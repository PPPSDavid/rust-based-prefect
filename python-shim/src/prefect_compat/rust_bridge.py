from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Any


def _platform_lib_names() -> list[str]:
    if sys.platform == "win32":
        return ["ironflow_engine.dll"]
    if sys.platform == "darwin":
        return ["libironflow_engine.dylib"]
    return ["libironflow_engine.so"]


def _find_repo_root_with_rust() -> Path | None:
    """Resolve IronFlow repo root when running from a source checkout (not site-packages)."""
    here = Path(__file__).resolve()
    for anc in here.parents:
        try:
            if (anc / "rust-engine" / "Cargo.toml").is_file() and (
                anc / "python-shim" / "pyproject.toml"
            ).is_file():
                return anc
        except OSError:
            continue
    return None


def _rust_target_paths(repo: Path) -> list[Path]:
    base = repo / "rust-engine" / "target"
    return [
        base / "release" / "ironflow_engine.dll",
        base / "debug" / "ironflow_engine.dll",
        base / "release" / "libironflow_engine.so",
        base / "debug" / "libironflow_engine.so",
        base / "release" / "libironflow_engine.dylib",
        base / "debug" / "libironflow_engine.dylib",
    ]


def _explicit_env_path() -> Path | None:
    env = os.getenv("IRONFLOW_RUST_LIB")
    if not env:
        return None
    return Path(env)


def _packaged_resource_exists() -> bool:
    try:
        from importlib import resources

        root = resources.files("prefect_compat") / "native"
        for name in _platform_lib_names():
            if (root / name).is_file():
                return True
    except (ModuleNotFoundError, FileNotFoundError, TypeError, ValueError):
        pass
    return False


def _try_load_packaged() -> ctypes.CDLL | None:
    from importlib import resources

    try:
        root = resources.files("prefect_compat") / "native"
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    for name in _platform_lib_names():
        target = root / name
        if not target.is_file():
            continue
        try:
            with resources.as_file(target) as p:
                lib = ctypes.CDLL(str(p))
                _configure_ironflow_symbols(lib)
                return lib
        except OSError:
            continue
    return None


def native_library_available() -> bool:
    """True when a prebuilt ``ironflow_engine`` cdylib exists on disk (release or debug)."""
    explicit = _explicit_env_path()
    if explicit is not None:
        return explicit.exists()
    repo = _find_repo_root_with_rust()
    if repo is not None:
        if any(p.exists() for p in _rust_target_paths(repo)):
            return True
    return _packaged_resource_exists()


_ironflow_lib: ctypes.CDLL | None = None


def load_ironflow_library() -> ctypes.CDLL:
    """Load the ironflow-engine cdylib once (shared by UI query bridge and FSM control)."""
    global _ironflow_lib
    if _ironflow_lib is not None:
        return _ironflow_lib

    explicit = _explicit_env_path()
    if explicit is not None:
        if explicit.exists():
            _ironflow_lib = ctypes.CDLL(str(explicit))
            _configure_ironflow_symbols(_ironflow_lib)
            return _ironflow_lib
        raise RuntimeError(
            f"IRONFLOW_RUST_LIB points to missing path: {explicit}. "
            "Unset it or point at a built ironflow_engine cdylib."
        )

    repo = _find_repo_root_with_rust()
    if repo is not None:
        for path in _rust_target_paths(repo):
            if path.exists():
                _ironflow_lib = ctypes.CDLL(str(path))
                _configure_ironflow_symbols(_ironflow_lib)
                return _ironflow_lib

    packaged = _try_load_packaged()
    if packaged is not None:
        _ironflow_lib = packaged
        return _ironflow_lib

    raise RuntimeError(
        "Rust ironflow_engine library not found. "
        "Install a wheel that bundles prefect_compat/native, build rust-engine in a repo checkout, "
        "or set IRONFLOW_RUST_LIB to the cdylib path."
    )


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

    if hasattr(lib, "ironflow_deployment_scheduler_start"):
        lib.ironflow_deployment_scheduler_start.argtypes = [
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.c_int64,
        ]
        lib.ironflow_deployment_scheduler_start.restype = ctypes.c_bool
    if hasattr(lib, "ironflow_deployment_scheduler_stop"):
        lib.ironflow_deployment_scheduler_stop.argtypes = [ctypes.c_uint64]
        lib.ironflow_deployment_scheduler_stop.restype = None


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
    """Native FSM (``Engine``) behind ``ironflow_engine_new`` / ``ironflow_control``.

    Rust serializes per-handle FFI calls; callers must also serialize Python-side writes
    (see ``InMemoryControlPlane._lock`` and ``bind_db`` guidance in ``runtime.py``).
    """

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

    def deployment_scheduler_start(
        self, handle: int, interval_ms: int, stale_after_seconds: int
    ) -> bool:
        fn = getattr(self._lib, "ironflow_deployment_scheduler_start", None)
        if fn is None:
            return False
        return bool(
            fn(
                ctypes.c_uint64(handle),
                ctypes.c_uint64(interval_ms),
                ctypes.c_int64(stale_after_seconds),
            )
        )

    def deployment_scheduler_stop(self, handle: int) -> None:
        fn = getattr(self._lib, "ironflow_deployment_scheduler_stop", None)
        if fn is not None:
            fn(ctypes.c_uint64(handle))
