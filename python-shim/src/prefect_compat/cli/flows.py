"""CLI for flow catalog lifecycle over ``/api/flows``."""

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


class FlowCatalogClient:
    """Thin HTTP client for flow catalog list/rename/archive/restore/delete."""

    def __init__(
        self, base_url: str = DEFAULT_API_URL, session: Any | None = None
    ) -> None:
        self._owned = DeployClient(base_url, session=session)
        self._session = self._owned._session

    def close(self) -> None:
        self._owned.close()

    def __enter__(self) -> FlowCatalogClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def list_flows(self, status: str | None = None) -> dict[str, Any]:
        path = "/api/flows"
        if status:
            path = f"/api/flows?status={quote(status, safe='')}"
        response = self._session.get(path)
        response.raise_for_status()
        return response.json()

    def get_flow(self, name_or_id: str) -> dict[str, Any]:
        encoded = quote(name_or_id, safe="")
        response = self._session.get(f"/api/flows/{encoded}")
        if response.status_code == 404:
            raise LookupError(f"flow not found: {name_or_id}")
        response.raise_for_status()
        return response.json()

    def rename_flow(self, flow_id: str, name: str) -> dict[str, Any]:
        return self._mutate(
            "POST", f"/api/flows/{quote(flow_id, safe='')}/rename", {"name": name}
        )

    def archive_flow(self, flow_id: str) -> dict[str, Any]:
        return self._mutate("POST", f"/api/flows/{quote(flow_id, safe='')}/archive")

    def restore_flow(self, flow_id: str) -> dict[str, Any]:
        return self._mutate("POST", f"/api/flows/{quote(flow_id, safe='')}/restore")

    def delete_flow(self, flow_id: str) -> dict[str, Any]:
        return self._mutate("DELETE", f"/api/flows/{quote(flow_id, safe='')}")

    def _mutate(
        self, method: str, path: str, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if method == "POST":
            response = self._session.post(path, json=json_body or {})
        else:
            response = self._session.delete(path)
        if response.status_code == 404:
            raise LookupError("flow not found")
        if response.status_code == 409:
            try:
                payload = response.json()
            except Exception:
                payload = {"message": "conflict"}
            raise RuntimeError(json.dumps(payload, default=str))
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"ok": True}


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _client_from_args(args: argparse.Namespace) -> FlowCatalogClient:
    return FlowCatalogClient(args.api_url or _default_api_url())


def cmd_flow_ls(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.list_flows(status=args.status))
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


def cmd_flow_inspect(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.get_flow(args.name))
        return 0
    except LookupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


def cmd_flow_rename(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.rename_flow(args.flow_id, args.name))
        return 0
    except (LookupError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


def cmd_flow_archive(args: argparse.Namespace) -> int:
    return _cmd_flow_action(args, "archive")


def cmd_flow_restore(args: argparse.Namespace) -> int:
    return _cmd_flow_action(args, "restore")


def cmd_flow_delete(args: argparse.Namespace) -> int:
    return _cmd_flow_action(args, "delete")


def _cmd_flow_action(args: argparse.Namespace, action: str) -> int:
    client = _client_from_args(args)
    try:
        if action == "archive":
            _print_json(client.archive_flow(args.flow_id))
        elif action == "restore":
            _print_json(client.restore_flow(args.flow_id))
        else:
            _print_json(client.delete_flow(args.flow_id))
        return 0
    except (LookupError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


def add_flow_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    api_parent = argparse.ArgumentParser(add_help=False)
    api_parent.add_argument(
        "--api-url",
        default=None,
        help=f"API base URL (default: IRONFLOW_API_URL or {DEFAULT_API_URL}).",
    )
    flow_parser = subparsers.add_parser(
        "flow",
        help="Flow catalog (list, rename, archive, restore, delete).",
    )
    flow_sub = flow_parser.add_subparsers(dest="flow_command", required=True)

    ls_parser = flow_sub.add_parser(
        "ls",
        parents=[api_parent],
        help="List catalog flows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow flow ls",
                "ironflow flow ls --status archived",
            ]
        ),
    )
    ls_parser.add_argument(
        "--status",
        default=None,
        choices=["active", "archived", "deleted"],
        help="Filter by catalog status (default: active when hide-archived is on).",
    )
    ls_parser.set_defaults(func=cmd_flow_ls)

    inspect_parser = flow_sub.add_parser(
        "inspect",
        parents=[api_parent],
        help="Show one flow by canonical name, alias, or id.",
    )
    inspect_parser.add_argument("name", help="Flow name, alias, or UUID.")
    inspect_parser.set_defaults(func=cmd_flow_inspect)

    rename_parser = flow_sub.add_parser(
        "rename",
        parents=[api_parent],
        help="Rename a flow in place (old name becomes an alias).",
    )
    rename_parser.add_argument("flow_id", help="Catalog UUID.")
    rename_parser.add_argument("name", help="New canonical name.")
    rename_parser.set_defaults(func=cmd_flow_rename)

    archive_parser = flow_sub.add_parser(
        "archive",
        parents=[api_parent],
        help="Archive a flow with no undeleted deployments.",
    )
    archive_parser.add_argument("flow_id", help="Catalog UUID.")
    archive_parser.set_defaults(func=cmd_flow_archive)

    restore_parser = flow_sub.add_parser(
        "restore",
        parents=[api_parent],
        help="Restore an archived or soft-deleted flow.",
    )
    restore_parser.add_argument("flow_id", help="Catalog UUID.")
    restore_parser.set_defaults(func=cmd_flow_restore)

    delete_parser = flow_sub.add_parser(
        "delete",
        parents=[api_parent],
        help="Soft-delete a flow with no undeleted deployments.",
    )
    delete_parser.add_argument("flow_id", help="Catalog UUID.")
    delete_parser.set_defaults(func=cmd_flow_delete)
