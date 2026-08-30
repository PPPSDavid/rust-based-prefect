from __future__ import annotations

import argparse
import os
import sys
import threading
from collections.abc import Callable, Sequence
from importlib import import_module, resources
from importlib.metadata import version
from pathlib import Path
from typing import Any

import yaml

from ..decorators import set_control_plane
from ..deploy.client import DEFAULT_WORK_POOL_NAME, DeployClient
from ..deploy.pull import run_pull_steps
from ..deploy.spec import DeploymentSpec, PullStepSpec, parse_entrypoint
from ..deploy.yaml_loader import load_manifest
from ..flow_registry import FLOW_REGISTRY
from ..runtime import InMemoryControlPlane
from ..services import run_services_loop
from ..worker import resolve_worker_mode, run_http_worker_loop, run_worker_loop
from ..worker_client import WorkerHttpClient
from .gcl import add_gcl_parser

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_MANIFEST = "ironflow.yaml"
DEFAULT_HISTORY_PATH = str(Path("data") / "ironflow_history.jsonl")


def _default_api_url() -> str:
    return os.getenv("IRONFLOW_API_URL", DEFAULT_API_URL).rstrip("/")


def _default_history_path() -> str:
    return os.getenv("IRONFLOW_HISTORY_PATH", DEFAULT_HISTORY_PATH)


def _epilog(examples: list[str]) -> str:
    lines = ["Examples:"] + [f"  {example}" for example in examples]
    return "\n".join(lines)


def _load_pull_steps_from_file(manifest_path: Path) -> list[PullStepSpec]:
    with manifest_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    pull_raw = raw.get("pull")
    if not pull_raw:
        return []
    if not isinstance(pull_raw, list):
        raise ValueError(f"{manifest_path}: 'pull' must be a list")
    return [PullStepSpec.model_validate(item) for item in pull_raw]


def _resolve_deployments(
    manifest_path: Path,
    *,
    name: str | None,
    deploy_all: bool,
) -> list[DeploymentSpec]:
    manifest = load_manifest(manifest_path)
    if not manifest.deployments:
        raise ValueError(f"{manifest_path}: no deployments defined")

    if deploy_all:
        return list(manifest.deployments)

    if name is None:
        if len(manifest.deployments) == 1:
            return [manifest.deployments[0]]
        names = ", ".join(spec.name for spec in manifest.deployments)
        raise ValueError(
            f"Multiple deployments in {manifest_path}; pass --name or --all. "
            f"Available: {names}"
        )

    for spec in manifest.deployments:
        if spec.name == name:
            return [spec]
    available = ", ".join(spec.name for spec in manifest.deployments)
    raise ValueError(
        f"Deployment {name!r} not found in {manifest_path}. Available: {available}"
    )


def _print_deploy_result(spec: DeploymentSpec, result: dict[str, Any]) -> None:
    action = result.get("action", "unknown")
    if result.get("dry_run"):
        print(f"dry-run: would {action} deployment {spec.name!r}")
        if result.get("deployment_id"):
            print(f"deployment_id: {result['deployment_id']}")
        return

    deployment = result.get("deployment")
    deployment_id = deployment.get("id") if isinstance(deployment, dict) else None
    print(f"{action}: deployment {spec.name!r}")
    if deployment_id:
        print(f"deployment_id: {deployment_id}")


def cmd_init(args: argparse.Namespace) -> int:
    target_dir = Path(args.directory).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = target_dir / DEFAULT_MANIFEST

    if manifest_path.exists():
        print(f"exists: {manifest_path}")
        return 0

    template_name = "ironflow.yaml"
    if args.recipe != "process":
        print(
            f"Warning: recipe {args.recipe!r} is not specialized yet; "
            f"writing default process template.",
            file=sys.stderr,
        )

    template_text = (
        resources.files("prefect_compat.cli.templates")
        .joinpath(template_name)
        .read_text(encoding="utf-8")
    )
    manifest_path.write_text(template_text, encoding="utf-8")
    print(f"created: {manifest_path}")
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    manifest_path = Path(args.file).resolve()
    if not manifest_path.is_file():
        print(
            f"Error: manifest not found: {manifest_path}\n"
            f"  ironflow init --directory {manifest_path.parent}\n"
            f"  ironflow deploy --file {manifest_path} --all",
            file=sys.stderr,
        )
        return 1

    try:
        specs = _resolve_deployments(
            manifest_path,
            name=args.name,
            deploy_all=args.all,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    api_url = args.api_url or _default_api_url()
    client = DeployClient(api_url)
    exit_code = 0
    try:
        for spec in specs:
            try:
                result = client.upsert_deployment(spec, dry_run=args.dry_run)
                _print_deploy_result(spec, result)
            except Exception as exc:
                print(f"Error deploying {spec.name!r}: {exc}", file=sys.stderr)
                exit_code = 1
    finally:
        client.close()
    return exit_code


def _build_flow_registry(spec: DeploymentSpec) -> dict[str, Callable[..., Any]]:
    entrypoint = spec.entrypoint
    if not entrypoint:
        if spec.flow_name and spec.flow_name in FLOW_REGISTRY:
            return {spec.flow_name: FLOW_REGISTRY[spec.flow_name]}
        raise ValueError(
            f"deployment {spec.name!r} has no entrypoint and flow {spec.flow_name!r} "
            "is not in the server FLOW_REGISTRY"
        )

    module_name, func_name = parse_entrypoint(entrypoint)
    mod = import_module(module_name)
    flow_fn = getattr(mod, func_name, None)
    if flow_fn is None:
        raise ValueError(f"entrypoint function not found: {entrypoint}")

    flow_name = spec.flow_name or func_name
    return {flow_name: flow_fn}


def _setup_local_control_plane(history_path: str | None = None) -> InMemoryControlPlane:
    path = history_path or _default_history_path()
    os.environ.setdefault("IRONFLOW_HISTORY_PATH", path)
    plane = InMemoryControlPlane(history_path=path)
    set_control_plane(plane)
    return plane


def _resolve_work_pool_id(
    client: DeployClient,
    spec: DeploymentSpec,
    *,
    pool_override: str | None,
) -> str:
    pool_name = pool_override or spec.work_pool_name or DEFAULT_WORK_POOL_NAME
    deployment = client.find_deployment_by_name(spec.name)
    if isinstance(deployment, dict) and deployment.get("work_pool_id"):
        return str(deployment["work_pool_id"])
    pool = client._find_work_pool_by_name(pool_name)
    if pool is not None:
        return str(pool["id"])
    return pool_name


def cmd_serve(args: argparse.Namespace) -> int:
    manifest_path = Path(args.file).resolve()
    if not manifest_path.is_file():
        print(f"Error: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    if args.all:
        print(
            "Error: serve supports one deployment; omit --all and pass --name.",
            file=sys.stderr,
        )
        return 1

    try:
        specs = _resolve_deployments(manifest_path, name=args.name, deploy_all=False)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    spec = specs[0]
    api_url = args.api_url or _default_api_url()
    client = DeployClient(api_url)
    try:
        result = client.upsert_deployment(spec, dry_run=False)
        _print_deploy_result(spec, result)
    except Exception as exc:
        print(f"Error deploying {spec.name!r}: {exc}", file=sys.stderr)
        client.close()
        return 1

    try:
        pull_steps = _load_pull_steps_from_file(manifest_path)
        if pull_steps:
            run_pull_steps(pull_steps)
        flow_registry = _build_flow_registry(spec)
        work_pool_id = _resolve_work_pool_id(
            client,
            spec,
            pool_override=args.pool,
        )
    except Exception as exc:
        print(f"Error preparing serve worker: {exc}", file=sys.stderr)
        client.close()
        return 1
    finally:
        client.close()

    worker_name = args.worker_name or f"serve-{spec.name}"
    stop_event = threading.Event()
    mode = resolve_worker_mode(getattr(args, "worker_mode", None))

    print(f"worker: {worker_name}")
    print(f"work_pool_id: {work_pool_id}")
    print(f"worker_mode: {mode}")

    try:
        if mode == "http":
            print(f"api_url: {api_url}")
            print(
                "Tip: HTTP mode does not open IRONFLOW_HISTORY_PATH; "
                "set IRONFLOW_ENABLE_LOCAL_WORKER=0 on the API server.",
                file=sys.stderr,
            )
            with WorkerHttpClient(api_url) as worker_client:
                run_http_worker_loop(
                    worker_client,
                    worker_name=worker_name,
                    work_pool_id=work_pool_id,
                    flow_registry=flow_registry,
                    stop_event=stop_event,
                )
        else:
            plane = _setup_local_control_plane()
            print(f"history_path: {plane._history_path}")
            print(
                "Tip: disable the in-process server worker with IRONFLOW_ENABLE_LOCAL_WORKER=0 "
                "when running a standalone serve/worker process.",
                file=sys.stderr,
            )
            run_worker_loop(
                plane,
                worker_name=worker_name,
                work_pool_id=work_pool_id,
                flow_registry=flow_registry,
                stop_event=stop_event,
            )
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    return 0


def cmd_worker_start(args: argparse.Namespace) -> int:
    manifest_path: Path | None = None
    if args.file:
        manifest_path = Path(args.file).resolve()
        if not manifest_path.is_file():
            print(f"Error: manifest not found: {manifest_path}", file=sys.stderr)
            return 1
        try:
            pull_steps = _load_pull_steps_from_file(manifest_path)
            if pull_steps:
                run_pull_steps(pull_steps)
        except Exception as exc:
            print(f"Error running pull steps: {exc}", file=sys.stderr)
            return 1

    pool_name = args.pool or DEFAULT_WORK_POOL_NAME
    worker_name = args.name or "ironflow-worker"
    work_pool_id = pool_name
    api_url = args.api_url or _default_api_url()
    mode = resolve_worker_mode(getattr(args, "worker_mode", None))

    if manifest_path is not None:
        try:
            specs = _resolve_deployments(
                manifest_path, name=args.name, deploy_all=False
            )
            work_pool_id = _resolve_work_pool_id(
                DeployClient(api_url),
                specs[0],
                pool_override=args.pool,
            )
        except ValueError:
            pass
        except Exception:
            pass

    stop_event = threading.Event()
    print(f"worker: {worker_name}")
    print(f"work_pool_id: {work_pool_id}")
    print(f"lease_seconds: {args.lease_seconds}")
    print(f"worker_mode: {mode}")

    try:
        if mode == "http":
            print(f"api_url: {api_url}")
            print(
                "Tip: HTTP mode needs only IRONFLOW_API_URL (+ auth); "
                "no shared IRONFLOW_HISTORY_PATH. Disable the server embed with "
                "IRONFLOW_ENABLE_LOCAL_WORKER=0.",
                file=sys.stderr,
            )
            with WorkerHttpClient(api_url) as worker_client:
                run_http_worker_loop(
                    worker_client,
                    worker_name=worker_name,
                    work_pool_id=work_pool_id,
                    flow_registry=FLOW_REGISTRY,
                    lease_seconds=args.lease_seconds,
                    stop_event=stop_event,
                )
        else:
            plane = _setup_local_control_plane()
            print(f"history_path: {plane._history_path}")
            print(
                "Tip: set IRONFLOW_HISTORY_PATH to the server data dir and "
                "IRONFLOW_ENABLE_LOCAL_WORKER=0 on the API server for split-process workers.",
                file=sys.stderr,
            )
            run_worker_loop(
                plane,
                worker_name=worker_name,
                work_pool_id=work_pool_id,
                flow_registry=FLOW_REGISTRY,
                lease_seconds=args.lease_seconds,
                stop_event=stop_event,
            )
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    try:
        pkg_version = version("ironflow-prefect-compat")
    except Exception:
        pkg_version = "0.0.0"

    parser = argparse.ArgumentParser(
        prog="ironflow",
        description="IronFlow CLI (init, deploy, serve, worker, gcl, server).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {pkg_version}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help="Write ironflow.yaml template if it does not exist.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow init",
                "ironflow init --directory ./deploy",
                "ironflow init --recipe process --directory .",
            ]
        ),
    )
    init_parser.add_argument(
        "--recipe",
        default="process",
        choices=["process"],
        help="Deployment recipe template (default: process).",
    )
    init_parser.add_argument(
        "--directory",
        default=".",
        help="Directory for ironflow.yaml (default: current directory).",
    )
    init_parser.set_defaults(func=cmd_init)

    deploy_parser = subparsers.add_parser(
        "deploy",
        help="Load manifest and upsert deployment(s) via the API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow deploy --file ironflow.yaml --name my-deployment",
                "ironflow deploy --file ironflow.yaml --all",
                "ironflow deploy --file ironflow.yaml --all --dry-run",
                "ironflow deploy --all --api-url http://127.0.0.1:8000",
            ]
        ),
    )
    deploy_parser.add_argument(
        "--file",
        default=DEFAULT_MANIFEST,
        help=f"Manifest path (default: {DEFAULT_MANIFEST}).",
    )
    deploy_parser.add_argument(
        "--name",
        help="Deploy a single deployment by name.",
    )
    deploy_parser.add_argument(
        "--all",
        action="store_true",
        help="Deploy every deployment in the manifest.",
    )
    deploy_parser.add_argument(
        "--api-url",
        default=None,
        help=f"API base URL (default: IRONFLOW_API_URL or {DEFAULT_API_URL}).",
    )
    deploy_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned create/update actions without mutating the API.",
    )
    deploy_parser.set_defaults(func=cmd_deploy)

    serve_parser = subparsers.add_parser(
        "serve",
        help="Deploy one deployment, run pull steps, then start a local worker loop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow serve --file ironflow.yaml --name my-deployment",
                "ironflow serve --name my-deployment --pool default-process-pool",
                "ironflow serve --name my-deployment --worker-name prod-worker-1",
            ]
        ),
    )
    serve_parser.add_argument(
        "--file",
        default=DEFAULT_MANIFEST,
        help=f"Manifest path (default: {DEFAULT_MANIFEST}).",
    )
    serve_parser.add_argument("--name", help="Deployment name to serve.")
    serve_parser.add_argument(
        "--api-url",
        default=None,
        help=f"API base URL (default: IRONFLOW_API_URL or {DEFAULT_API_URL}).",
    )
    serve_parser.add_argument(
        "--pool",
        default=None,
        help=f"Work pool name override (default: manifest or {DEFAULT_WORK_POOL_NAME}).",
    )
    serve_parser.add_argument(
        "--worker-name",
        default=None,
        help="Worker name for claims (default: serve-<deployment-name>).",
    )
    serve_parser.add_argument(
        "--worker-mode",
        default=None,
        choices=["http", "file"],
        help="Claim transport: http (API only) or file (shared DB). "
        "Default: IRONFLOW_WORKER_MODE or file.",
    )
    serve_parser.set_defaults(func=cmd_serve)

    worker_parser = subparsers.add_parser(
        "worker",
        help="Run a standalone worker (file mode shared DB, or HTTP claim API).",
    )
    worker_sub = worker_parser.add_subparsers(dest="worker_command", required=True)

    worker_start = worker_sub.add_parser(
        "start",
        help="Optionally run manifest pull steps, then poll for deployment runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow worker start --pool default-process-pool",
                "ironflow worker start --name worker-1 --lease-seconds 30",
                "ironflow worker start --file ironflow.yaml",
                "IRONFLOW_WORKER_MODE=http IRONFLOW_API_URL=http://127.0.0.1:8000 "
                "ironflow worker start",
                "IRONFLOW_HISTORY_PATH=data/ironflow_history.jsonl ironflow worker start",
            ]
        ),
    )
    worker_start.add_argument(
        "--pool",
        default=None,
        help=f"Work pool name (default: {DEFAULT_WORK_POOL_NAME}).",
    )
    worker_start.add_argument(
        "--name",
        default=None,
        help="Worker name (default: ironflow-worker).",
    )
    worker_start.add_argument(
        "--lease-seconds",
        type=int,
        default=30,
        help="Claim lease duration in seconds (default: 30).",
    )
    worker_start.add_argument(
        "--api-url",
        default=None,
        help=f"API base URL (HTTP mode + pool resolve; default: {DEFAULT_API_URL}).",
    )
    worker_start.add_argument(
        "--file",
        default=None,
        help="Optional manifest for pull steps before starting the worker.",
    )
    worker_start.add_argument(
        "--worker-mode",
        default=None,
        choices=["http", "file"],
        help="Claim transport: http (API only) or file (shared DB). "
        "Default: IRONFLOW_WORKER_MODE or file.",
    )
    worker_start.set_defaults(func=cmd_worker_start)

    server_parser = subparsers.add_parser(
        "server",
        help="Server-side processes (API is uvicorn; background services live here).",
    )
    server_sub = server_parser.add_subparsers(dest="server_command", required=True)
    services_parser = server_sub.add_parser(
        "services",
        help="Background maintenance (scheduler / lease reclaim).",
    )
    services_sub = services_parser.add_subparsers(
        dest="services_command", required=True
    )
    services_start = services_sub.add_parser(
        "start",
        help="Run schedule ticks and maintenance (no HTTP listener).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow server services start",
                "IRONFLOW_DATABASE_URL=postgresql://… ironflow server services start",
            ]
        ),
    )
    services_start.add_argument(
        "--interval-ms",
        type=int,
        default=None,
        help="Tick interval ms (default: IRONFLOW_SCHEDULER_INTERVAL_MS or 1000).",
    )
    services_start.add_argument(
        "--stale-seconds",
        type=int,
        default=None,
        help="Stale worker threshold (default: IRONFLOW_SCHEDULER_STALE_SECONDS or 120).",
    )
    services_start.set_defaults(func=cmd_server_services_start)

    add_gcl_parser(subparsers)

    return parser


def cmd_server_services_start(args: argparse.Namespace) -> int:
    plane = _setup_local_control_plane()
    stop_event = threading.Event()
    interval = args.interval_ms
    stale = args.stale_seconds
    print("ironflow server services start")
    print(f"history_path: {plane._history_path}")
    if getattr(plane, "_store", None) is not None:
        print(f"store_backend: {plane._store.backend_kind}")
    print(
        "Note: run a single services replica per stack (HA advisory lock deferred).",
        file=sys.stderr,
    )
    try:
        run_services_loop(
            plane,
            interval_ms=interval,
            stale_after_seconds=stale,
            stop_event=stop_event,
        )
    except KeyboardInterrupt:
        stop_event.set()
        print("\nstopped", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    func: Callable[[argparse.Namespace], int] = args.func
    code = func(args)
    if argv is None:
        raise SystemExit(code)
    return code


if __name__ == "__main__":
    main()
