"""CLI for global concurrency limits over ``/api/concurrency-limits``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.parse import quote

from ..deploy.client import DeployClient

DEFAULT_API_URL = "http://127.0.0.1:8000"


def _default_api_url() -> str:
    return os.getenv("IRONFLOW_API_URL", DEFAULT_API_URL).rstrip("/")


def _epilog(examples: list[str]) -> str:
    lines = ["Examples:"] + [f"  {example}" for example in examples]
    return "\n".join(lines)


class GclClient:
    """Thin HTTP client for concurrency-limit CRUD (same ledger as the UI)."""

    def __init__(
        self, base_url: str = DEFAULT_API_URL, session: Any | None = None
    ) -> None:
        self._owned = DeployClient(base_url, session=session)
        self._session = self._owned._session

    def close(self) -> None:
        self._owned.close()

    def __enter__(self) -> GclClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def list_limits(self) -> list[dict[str, Any]]:
        response = self._session.get("/api/concurrency-limits")
        response.raise_for_status()
        payload = response.json() or {}
        return list(payload.get("limits") or [])

    def get_limit(self, name: str) -> dict[str, Any]:
        encoded = quote(name, safe="")
        response = self._session.get(f"/api/concurrency-limits/{encoded}")
        if response.status_code == 404:
            raise LookupError(f"concurrency limit not found: {name}")
        response.raise_for_status()
        return response.json()

    def create_limit(
        self,
        name: str,
        limit: int,
        *,
        slot_decay_per_second: float | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "limit": limit, "active": active}
        if slot_decay_per_second is not None:
            body["slot_decay_per_second"] = slot_decay_per_second
        response = self._session.post("/api/concurrency-limits", json=body)
        response.raise_for_status()
        return response.json()

    def update_limit(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        encoded = quote(name, safe="")
        response = self._session.patch(f"/api/concurrency-limits/{encoded}", json=body)
        if response.status_code == 404:
            raise LookupError(f"concurrency limit not found: {name}")
        response.raise_for_status()
        return response.json()

    def delete_limit(self, name: str) -> dict[str, Any]:
        encoded = quote(name, safe="")
        response = self._session.delete(f"/api/concurrency-limits/{encoded}")
        if response.status_code == 404:
            raise LookupError(f"concurrency limit not found: {name}")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"deleted": True, "name": name}


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _client_from_args(args: argparse.Namespace) -> GclClient:
    return GclClient(args.api_url or _default_api_url())


def cmd_gcl_ls(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.list_limits())
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("  ironflow gcl ls --api-url http://127.0.0.1:8000", file=sys.stderr)
        return 1
    finally:
        client.close()


def cmd_gcl_inspect(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.get_limit(args.name))
        return 0
    except LookupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(f"  ironflow gcl inspect {args.name}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


def cmd_gcl_create(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        created = client.create_limit(
            args.name,
            args.limit,
            slot_decay_per_second=args.decay,
            active=not args.inactive,
        )
        _print_json(created)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(
            "  ironflow gcl create db --limit 5 --api-url http://127.0.0.1:8000",
            file=sys.stderr,
        )
        return 1
    finally:
        client.close()


def cmd_gcl_update(args: argparse.Namespace) -> int:
    body: dict[str, Any] = {}
    if args.limit is not None:
        body["limit"] = args.limit
    if args.decay is not None:
        body["slot_decay_per_second"] = args.decay
    if args.active:
        body["active"] = True
    if args.inactive:
        body["active"] = False
    if not body:
        print(
            "Error: pass --limit, --decay, --active, and/or --inactive.\n"
            "  ironflow gcl update db --limit 8",
            file=sys.stderr,
        )
        return 1
    client = _client_from_args(args)
    try:
        _print_json(client.update_limit(args.name, body))
        return 0
    except LookupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


def cmd_gcl_delete(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.delete_limit(args.name))
        return 0
    except LookupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(f"  ironflow gcl delete {args.name}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


def add_gcl_parser(subparsers: Any) -> None:
    api_parent = argparse.ArgumentParser(add_help=False)
    api_parent.add_argument(
        "--api-url",
        default=None,
        help=f"API base URL (default: IRONFLOW_API_URL or {DEFAULT_API_URL}).",
    )

    gcl_parser = subparsers.add_parser(
        "gcl",
        help="Manage global concurrency limits via the HTTP API.",
        description=(
            "CRUD for named slot limits (same /api/concurrency-limits ledger as the UI). "
            "Not work-pool or per-deployment concurrency."
        ),
    )
    gcl_sub = gcl_parser.add_subparsers(dest="gcl_command", required=True)

    ls_parser = gcl_sub.add_parser(
        "ls",
        parents=[api_parent],
        help="List concurrency limits as JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow gcl ls",
                "ironflow gcl ls --api-url http://127.0.0.1:8000",
            ]
        ),
    )
    ls_parser.set_defaults(func=cmd_gcl_ls)

    inspect_parser = gcl_sub.add_parser(
        "inspect",
        parents=[api_parent],
        help="Show one limit (name, limit, active_slots, decay).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow gcl inspect db",
                "ironflow gcl inspect tag:db",
            ]
        ),
    )
    inspect_parser.add_argument("name", help="Limit name (tag limits use tag:<tag>).")
    inspect_parser.set_defaults(func=cmd_gcl_inspect)

    create_parser = gcl_sub.add_parser(
        "create",
        parents=[api_parent],
        help="Create or upsert a named limit.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow gcl create db --limit 5",
                "ironflow gcl create api --limit 10 --decay 2.0",
                "ironflow gcl create db --limit 0 --inactive",
            ]
        ),
    )
    create_parser.add_argument("name", help="Limit name.")
    create_parser.add_argument(
        "--limit",
        type=int,
        required=True,
        help="Maximum slots (0 denies acquire).",
    )
    create_parser.add_argument(
        "--decay",
        type=float,
        default=None,
        help="slot_decay_per_second for rate_limit mode.",
    )
    create_parser.add_argument(
        "--inactive",
        action="store_true",
        help="Create the limit as inactive (soft-bypass unless strict).",
    )
    create_parser.set_defaults(func=cmd_gcl_create)

    update_parser = gcl_sub.add_parser(
        "update",
        parents=[api_parent],
        help="Patch limit, decay, and/or active flag.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow gcl update db --limit 8",
                "ironflow gcl update api --decay 1.5 --active",
                "ironflow gcl update db --inactive",
            ]
        ),
    )
    update_parser.add_argument("name", help="Limit name.")
    update_parser.add_argument("--limit", type=int, default=None, help="New slot cap.")
    update_parser.add_argument(
        "--decay",
        type=float,
        default=None,
        help="New slot_decay_per_second.",
    )
    active_group = update_parser.add_mutually_exclusive_group()
    active_group.add_argument(
        "--active",
        action="store_true",
        help="Mark the limit active.",
    )
    active_group.add_argument(
        "--inactive",
        action="store_true",
        help="Mark the limit inactive.",
    )
    update_parser.set_defaults(func=cmd_gcl_update)

    delete_parser = gcl_sub.add_parser(
        "delete",
        parents=[api_parent],
        help="Delete a named limit (no prompt).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow gcl delete db",
                "ironflow gcl delete tag:db --api-url http://127.0.0.1:8000",
            ]
        ),
    )
    delete_parser.add_argument("name", help="Limit name.")
    delete_parser.set_defaults(func=cmd_gcl_delete)
