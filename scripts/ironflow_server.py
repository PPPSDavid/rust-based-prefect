from __future__ import annotations

import argparse
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
    return subprocess.Popen(cmd, cwd=repo_root)


def _start_frontend(repo_root: Path, frontend_port: int) -> subprocess.Popen[str]:
    npm_cmd = _resolve_npm_command()
    if npm_cmd is None:
        raise RuntimeError("npm not found. Install Node.js/npm or add npm to PATH.")

    frontend_dir = repo_root / "frontend"
    if not frontend_dir.exists():
        raise RuntimeError("frontend directory not found.")

    subprocess.run([*npm_cmd, "install"], cwd=frontend_dir, check=True)
    env = dict(os.environ, PORT=str(frontend_port))
    return subprocess.Popen([*npm_cmd, "run", "dev"], cwd=frontend_dir, env=env)


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
        print("\nStopping IronFlow services...")
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
    parser = argparse.ArgumentParser(description="IronFlow local server helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser(
        "start",
        help="Start IronFlow API and optional UI (similar to `prefect server start`).",
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
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
