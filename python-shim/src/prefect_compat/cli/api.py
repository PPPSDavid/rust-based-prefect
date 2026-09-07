"""CLI raw HTTP passthrough: ``ironflow api METHOD PATH``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..deploy.client import DeployClient

DEFAULT_API_URL = "http://127.0.0.1:8000"
_ALLOWED_METHODS = frozenset({"GET", "POST", "PATCH", "DELETE"})


def _default_api_url() -> str:
    return os.getenv("IRONFLOW_API_URL", DEFAULT_API_URL).rstrip("/")


def _epilog(examples: list[str]) -> str:
    lines = ["Examples:"] + [f"  {example}" for example in examples]
    return "\n".join(lines)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _normalize_path(path: str) -> str:
    path = path.strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _load_data(raw: str | None) -> dict[str, Any] | list[Any] | None:
    if raw is None:
        return None
    if raw == "-":
        text = sys.stdin.read()
    elif raw.startswith("@"):
        file_path = Path(raw[1:])
        text = file_path.read_text(encoding="utf-8")
    else:
        text = raw
    text = text.strip()
    if not text:
        return None
    payload = json.loads(text)
    if not isinstance(payload, (dict, list)):
        raise ValueError("--data must decode to a JSON object or array")
    return payload


def _append_query(path: str, query_pairs: list[str] | None) -> str:
    if not query_pairs:
        return path
    params: list[tuple[str, str]] = []
    for item in query_pairs:
        if "=" not in item:
            raise ValueError(f"invalid --query {item!r}; expected key=value")
        key, value = item.split("=", 1)
        params.append((key, value))
    encoded = urlencode(params)
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}{encoded}"


class ApiClient:
    """Thin HTTP client for arbitrary JSON requests against the IronFlow API."""

    def __init__(
        self, base_url: str = DEFAULT_API_URL, session: Any | None = None
    ) -> None:
        self._owned = DeployClient(base_url, session=session)
        self._session = self._owned._session

    def close(self) -> None:
        self._owned.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | list[Any] | None = None,
    ) -> tuple[int, Any]:
        method = method.upper()
        if method not in _ALLOWED_METHODS:
            raise ValueError(f"unsupported method: {method}")
        path = _normalize_path(path)
        if method == "GET":
            response = self._session.get(path)
        elif method == "POST":
            response = self._session.post(path, json=json_body)
        elif method == "PATCH":
            response = self._session.patch(path, json=json_body)
        else:
            response = self._session.delete(path)
        try:
            payload: Any = response.json()
        except Exception:
            payload = {"raw": getattr(response, "text", None) or ""}
        return int(response.status_code), payload


def _client_from_args(args: argparse.Namespace) -> ApiClient:
    return ApiClient(args.api_url or _default_api_url())


def cmd_api(args: argparse.Namespace) -> int:
    try:
        body = _load_data(args.data)
        path = _append_query(args.path, args.query)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    method = args.method.upper()
    if method in {"POST", "PATCH"} and body is not None and not isinstance(body, dict):
        # DeployClient / urllib session only accept dict JSON bodies.
        print("Error: --data for POST/PATCH must be a JSON object", file=sys.stderr)
        return 1

    client = _client_from_args(args)
    try:
        status, payload = client.request(
            method,
            path,
            json_body=body if isinstance(body, dict) else None,
        )
        if 200 <= status < 300:
            _print_json(payload)
            return 0
        print(json.dumps(payload, indent=2, default=str), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


def add_api_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    api_parser = subparsers.add_parser(
        "api",
        help="Raw JSON HTTP passthrough to the IronFlow API (agent-friendly).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow api GET /api/flow-runs",
                "ironflow api GET /api/flow-runs --query state=FAILED --query limit=10",
                "ironflow api POST /api/deployments/ID/run --data '{}'",
                'ironflow api POST /api/flow-runs/ID/pause --data \'{"mode":"drain"}\'',
                "ironflow api POST /api/deployments --data @body.json",
                "ironflow api DELETE /api/concurrency-limits/db",
            ]
        ),
    )
    api_parser.add_argument(
        "method",
        choices=["GET", "POST", "PATCH", "DELETE", "get", "post", "patch", "delete"],
        help="HTTP method.",
    )
    api_parser.add_argument(
        "path",
        help="API path (e.g. /api/flow-runs or api/flow-runs).",
    )
    api_parser.add_argument(
        "--data",
        default=None,
        help="JSON body string, @file, or - for stdin (POST/PATCH).",
    )
    api_parser.add_argument(
        "--query",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help="Query parameter (repeatable).",
    )
    api_parser.add_argument(
        "--api-url",
        default=None,
        help=f"API base URL (default: IRONFLOW_API_URL or {DEFAULT_API_URL}).",
    )
    api_parser.set_defaults(func=cmd_api)
