"""CLI for deployment list/inspect/run over ``/api/deployments``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID

from ..deploy.client import DeployClient

DEFAULT_API_URL = "http://127.0.0.1:8000"


def _default_api_url() -> str:
    return os.getenv("IRONFLOW_API_URL", DEFAULT_API_URL).rstrip("/")


def _epilog(examples: list[str]) -> str:
    lines = ["Examples:"] + [f"  {example}" for example in examples]
    return "\n".join(lines)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _looks_like_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _parse_param(item: str) -> tuple[str, Any]:
    if "=" not in item:
        raise ValueError(f"invalid --param {item!r}; expected key=value")
    key, raw = item.split("=", 1)
    try:
        return key, json.loads(raw)
    except json.JSONDecodeError:
        return key, raw


class DeploymentCliClient:
    """Thin HTTP client for deployment list/inspect/run."""

    def __init__(
        self, base_url: str = DEFAULT_API_URL, session: Any | None = None
    ) -> None:
        self._owned = DeployClient(base_url, session=session)
        self._session = self._owned._session

    def close(self) -> None:
        self._owned.close()

    def __enter__(self) -> DeploymentCliClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def list_deployments(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        path = f"/api/deployments?{urlencode(params)}"
        response = self._session.get(path)
        response.raise_for_status()
        return response.json()

    def get_deployment(self, name_or_id: str) -> dict[str, Any]:
        if _looks_like_uuid(name_or_id):
            encoded = quote(name_or_id, safe="")
            response = self._session.get(f"/api/deployments/{encoded}")
            if response.status_code == 404:
                raise LookupError(f"deployment not found: {name_or_id}")
            response.raise_for_status()
            return response.json()
        encoded = quote(name_or_id, safe="")
        response = self._session.get(f"/api/deployments/by-name/{encoded}")
        if response.status_code == 404:
            raise LookupError(f"deployment not found: {name_or_id}")
        response.raise_for_status()
        return response.json()

    def run_deployment(
        self,
        name_or_id: str,
        *,
        parameters: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        deployment = self.get_deployment(name_or_id)
        deployment_id = str(deployment["id"])
        body: dict[str, Any] = {}
        if parameters is not None:
            body["parameters"] = parameters
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        response = self._session.post(
            f"/api/deployments/{quote(deployment_id, safe='')}/run",
            json=body,
        )
        if response.status_code == 404:
            raise LookupError(f"deployment not found: {name_or_id}")
        if response.status_code in {400, 409, 422}:
            try:
                payload = response.json()
            except Exception:
                payload = {"detail": "request failed"}
            raise RuntimeError(json.dumps(payload, default=str))
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"ok": True}


def _client_from_args(args: argparse.Namespace) -> DeploymentCliClient:
    return DeploymentCliClient(args.api_url or _default_api_url())


def _handle_errors(exc: Exception) -> int:
    print(f"Error: {exc}", file=sys.stderr)
    return 1


def cmd_deployment_ls(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.list_deployments(limit=args.limit, cursor=args.cursor))
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def cmd_deployment_inspect(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.get_deployment(args.name_or_id))
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def cmd_deployment_run(args: argparse.Namespace) -> int:
    parameters: dict[str, Any] = {}
    if args.parameters:
        try:
            parsed = json.loads(args.parameters)
        except json.JSONDecodeError as exc:
            print(f"Error: --parameters is not valid JSON: {exc}", file=sys.stderr)
            return 1
        if not isinstance(parsed, dict):
            print("Error: --parameters must be a JSON object", file=sys.stderr)
            return 1
        parameters.update(parsed)
    if args.param:
        try:
            for item in args.param:
                key, value = _parse_param(item)
                parameters[key] = value
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    client = _client_from_args(args)
    try:
        _print_json(
            client.run_deployment(
                args.name_or_id,
                parameters=parameters or None,
                idempotency_key=args.idempotency_key,
            )
        )
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def add_deployment_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    api_parent = argparse.ArgumentParser(add_help=False)
    api_parent.add_argument(
        "--api-url",
        default=None,
        help=f"API base URL (default: IRONFLOW_API_URL or {DEFAULT_API_URL}).",
    )
    deployment_parser = subparsers.add_parser(
        "deployment",
        help="List, inspect, and trigger deployments (JSON output).",
    )
    deployment_sub = deployment_parser.add_subparsers(
        dest="deployment_command", required=True
    )

    ls_parser = deployment_sub.add_parser(
        "ls",
        parents=[api_parent],
        help="List deployments.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(["ironflow deployment ls", "ironflow deployment ls --limit 20"]),
    )
    ls_parser.add_argument("--limit", type=int, default=50)
    ls_parser.add_argument("--cursor", default=None)
    ls_parser.set_defaults(func=cmd_deployment_ls)

    inspect_parser = deployment_sub.add_parser(
        "inspect",
        parents=[api_parent],
        help="Show one deployment by name or UUID.",
    )
    inspect_parser.add_argument("name_or_id", help="Deployment name or UUID.")
    inspect_parser.set_defaults(func=cmd_deployment_inspect)

    run_parser = deployment_sub.add_parser(
        "run",
        parents=[api_parent],
        help="Trigger a deployment run (returns the run handle immediately).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow deployment run my-deployment",
                "ironflow deployment run my-deployment --param n=2",
                "ironflow deployment run my-deployment --parameters '{\"n\": 2}'",
                "ironflow deployment run <uuid> --idempotency-key once-1",
            ]
        ),
    )
    run_parser.add_argument("name_or_id", help="Deployment name or UUID.")
    run_parser.add_argument(
        "--parameters",
        default=None,
        help="JSON object of parameters.",
    )
    run_parser.add_argument(
        "--param",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help="Single parameter (JSON-decoded when possible; repeatable).",
    )
    run_parser.add_argument(
        "--idempotency-key",
        default=None,
        help="Optional idempotency key for the trigger.",
    )
    run_parser.set_defaults(func=cmd_deployment_run)
