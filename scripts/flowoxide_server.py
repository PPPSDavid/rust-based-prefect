from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


def _resolve_npm_command() -> list[str] | None:
    npm = shutil.which("npm")
    if npm:
        return [npm]
    windows_fallback = Path(r"C:\Program Files\nodejs\npm.cmd")
    if windows_fallback.exists():
        return [str(windows_fallback)]
    return None


def _resolve_node_executable() -> str | None:
    node = shutil.which("node")
    if node:
        return node
    windows_fallback = Path(r"C:\Program Files\nodejs\node.exe")
    if windows_fallback.exists():
        return str(windows_fallback)
    return None


def _start_backend(repo_root: Path, host: str, port: int) -> subprocess.Popen[str]:
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "python-shim.src.prefect_compat.server:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    return subprocess.Popen(cmd, cwd=repo_root, text=True)


def _start_frontend(repo_root: Path, frontend_port: int) -> subprocess.Popen[str]:
    npm_cmd = _resolve_npm_command()
    if npm_cmd is None:
        raise RuntimeError("npm not found. Install Node.js/npm or add npm to PATH.")

    frontend_dir = repo_root / "frontend"
    if not frontend_dir.exists():
        raise RuntimeError("frontend directory not found.")

    subprocess.run([*npm_cmd, "install"], cwd=frontend_dir, check=True)
    env = dict(os.environ, PORT=str(frontend_port))
    return subprocess.Popen(
        [*npm_cmd, "run", "dev"], cwd=frontend_dir, env=env, text=True
    )


def _backend_status(repo_root: Path) -> str:
    server_module = repo_root / "python-shim" / "src" / "prefect_compat" / "server.py"
    if not server_module.exists():
        return "server-module-missing"
    if shutil.which(sys.executable) is None:
        return "python-missing"
    if importlib.util.find_spec("fastapi") is None:
        return "fastapi-missing"
    if importlib.util.find_spec("uvicorn") is None:
        return "uvicorn-missing"
    return "ready"


def _frontend_status(repo_root: Path) -> str:
    frontend_dir = repo_root / "frontend"
    if not frontend_dir.exists():
        return "frontend-missing"
    if not (frontend_dir / "package.json").exists():
        return "package-json-missing"
    node = _resolve_node_executable()
    if node is None:
        return "node-missing"
    if _resolve_npm_command() is None:
        return "npm-missing"
    return "ready"


def _rust_library_status(repo_root: Path) -> str:
    env_override = os.getenv("FLOWOXIDE_RUST_LIB")
    if env_override:
        candidate = Path(env_override)
        if candidate.exists():
            return "ready"
        return "env-path-missing"

    cargo_manifest = repo_root / "rust-engine" / "Cargo.toml"
    if not cargo_manifest.exists():
        return "cargo-manifest-missing"
    candidates = [
        repo_root / "rust-engine" / "target" / "release" / "flowoxide_engine.dll",
        repo_root / "rust-engine" / "target" / "debug" / "flowoxide_engine.dll",
        repo_root / "rust-engine" / "target" / "release" / "libflowoxide_engine.so",
        repo_root / "rust-engine" / "target" / "debug" / "libflowoxide_engine.so",
        repo_root / "rust-engine" / "target" / "release" / "libflowoxide_engine.dylib",
        repo_root / "rust-engine" / "target" / "debug" / "libflowoxide_engine.dylib",
    ]
    if any(path.exists() for path in candidates):
        return "ready"
    if shutil.which("cargo") is None:
        return "cargo-missing"
    return "not-built"


def _remediation(repo_root: Path, snapshot: dict[str, str]) -> list[str]:
    hints: list[str] = []
    back = snapshot["backend_status"]
    front = snapshot["frontend_status"]
    rust = snapshot["rust_library"]

    if back == "fastapi-missing" or back == "uvicorn-missing":
        hints.append("Install API deps: python -m pip install -r requirements-ci.txt")

    if front == "node-missing":
        hints.append("Install Node.js LTS (includes npm) and reopen your shell.")
    elif front == "npm-missing":
        hints.append(
            "npm not found on PATH. If Node is installed, ensure npm.cmd is on PATH."
        )
    elif front == "package-json-missing":
        hints.append(
            "frontend/package.json missing — verify you have a full repo checkout."
        )

    if rust == "env-path-missing":
        hints.append(
            "FLOWOXIDE_RUST_LIB points to a missing file — fix the path or unset it."
        )
    elif rust == "cargo-missing":
        hints.append("Install Rust via https://rustup.rs and reopen your shell.")
    elif rust == "not-built":
        hints.append(
            "Build rust-engine: cargo build --manifest-path rust-engine/Cargo.toml"
        )

    if not hints:
        return []

    deduped: list[str] = []
    for item in hints:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _doctor_snapshot(repo_root: Path) -> dict[str, str]:
    return {
        "backend_status": _backend_status(repo_root),
        "frontend_status": _frontend_status(repo_root),
        "rust_library": _rust_library_status(repo_root),
    }


def doctor(_: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    snapshot = _doctor_snapshot(repo_root)
    payload: dict[str, object] = dict(snapshot)
    remediation = _remediation(repo_root, snapshot)
    if remediation:
        payload["remediation"] = remediation
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if all(value == "ready" for value in snapshot.values()) else 1


def start(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]

    backend = _start_backend(repo_root, args.host, args.backend_port)
    frontend = None

    try:
        print(f"Backend starting on http://{args.host}:{args.backend_port}")
        if not args.backend_only:
            frontend = _start_frontend(repo_root, args.frontend_port)
            print(f"Frontend starting on http://localhost:{args.frontend_port}")
        print("Press Ctrl+C to stop.")
        while True:
            if backend.poll() is not None:
                return backend.returncode or 1
            if frontend is not None and frontend.poll() is not None:
                return frontend.returncode or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping FlowOxide services...")
    finally:
        if frontend is not None and frontend.poll() is None:
            frontend.send_signal(signal.SIGINT)
            try:
                frontend.wait(timeout=5)
            except subprocess.TimeoutExpired:
                frontend.terminate()
        if backend.poll() is None:
            backend.send_signal(signal.SIGINT)
            try:
                backend.wait(timeout=5)
            except subprocess.TimeoutExpired:
                backend.terminate()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FlowOxide local server helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser(
        "start",
        help="Start FlowOxide API and optional UI (similar to `prefect server start`).",
    )
    start_parser.add_argument("--host", default="127.0.0.1")
    start_parser.add_argument("--backend-port", type=int, default=8000)
    start_parser.add_argument("--frontend-port", type=int, default=4173)
    start_parser.add_argument(
        "--backend-only",
        action="store_true",
        help="Start only API backend.",
    )
    start_parser.set_defaults(func=start)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Print backend/frontend/rust readiness diagnostics.",
    )
    doctor_parser.set_defaults(func=doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
