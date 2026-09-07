"""CLI for flow-run inspect and lifecycle over ``/api/flow-runs``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.parse import quote, urlencode

from ..deploy.client import DeployClient

DEFAULT_API_URL = "http://127.0.0.1:8000"


def _default_api_url() -> str:
    return os.getenv("IRONFLOW_API_URL", DEFAULT_API_URL).rstrip("/")


def _epilog(examples: list[str]) -> str:
    lines = ["Examples:"] + [f"  {example}" for example in examples]
    return "\n".join(lines)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


class FlowRunClient:
    """Thin HTTP client for flow-run queries and lifecycle actions."""

    def __init__(
        self, base_url: str = DEFAULT_API_URL, session: Any | None = None
    ) -> None:
        self._owned = DeployClient(base_url, session=session)
        self._session = self._owned._session

    def close(self) -> None:
        self._owned.close()

    def __enter__(self) -> FlowRunClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def list_flow_runs(
        self,
        *,
        state: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"limit": str(limit)}
        if state:
            params["state"] = state
        if cursor:
            params["cursor"] = cursor
        if include_archived:
            params["include_archived"] = "true"
        path = f"/api/flow-runs?{urlencode(params)}"
        response = self._session.get(path)
        response.raise_for_status()
        return response.json()

    def get_flow_run(self, flow_run_id: str) -> dict[str, Any]:
        encoded = quote(flow_run_id, safe="")
        response = self._session.get(f"/api/flow-runs/{encoded}")
        if response.status_code == 404:
            raise LookupError(f"flow run not found: {flow_run_id}")
        response.raise_for_status()
        return response.json()

    def list_task_runs(
        self,
        flow_run_id: str,
        *,
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        encoded = quote(flow_run_id, safe="")
        path = f"/api/flow-runs/{encoded}/task-runs?{urlencode(params)}"
        response = self._session.get(path)
        if response.status_code == 404:
            raise LookupError(f"flow run not found: {flow_run_id}")
        response.raise_for_status()
        return response.json()

    def list_logs(
        self,
        flow_run_id: str,
        *,
        task_run_id: str | None = None,
        level: str | None = None,
        limit: int = 500,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"limit": str(limit)}
        if task_run_id:
            params["task_run_id"] = task_run_id
        if level:
            params["level"] = level
        if cursor:
            params["cursor"] = cursor
        encoded = quote(flow_run_id, safe="")
        path = f"/api/flow-runs/{encoded}/logs?{urlencode(params)}"
        response = self._session.get(path)
        if response.status_code == 404:
            raise LookupError(f"flow run not found: {flow_run_id}")
        response.raise_for_status()
        return response.json()

    def list_events(
        self,
        flow_run_id: str,
        *,
        limit: int = 500,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        encoded = quote(flow_run_id, safe="")
        path = f"/api/flow-runs/{encoded}/events?{urlencode(params)}"
        response = self._session.get(path)
        if response.status_code == 404:
            raise LookupError(f"flow run not found: {flow_run_id}")
        response.raise_for_status()
        return response.json()

    def get_dag(self, flow_run_id: str, *, mode: str = "logical") -> dict[str, Any]:
        encoded = quote(flow_run_id, safe="")
        path = f"/api/flow-runs/{encoded}/dag?{urlencode({'mode': mode})}"
        response = self._session.get(path)
        if response.status_code == 404:
            raise LookupError(f"flow run not found: {flow_run_id}")
        response.raise_for_status()
        return response.json()

    def cancel(self, flow_run_id: str) -> dict[str, Any]:
        return self._lifecycle("POST", flow_run_id, "cancel")

    def pause(self, flow_run_id: str, *, mode: str) -> dict[str, Any]:
        return self._lifecycle("POST", flow_run_id, "pause", {"mode": mode})

    def resume(self, flow_run_id: str) -> dict[str, Any]:
        return self._lifecycle("POST", flow_run_id, "resume")

    def retry(self, flow_run_id: str) -> dict[str, Any]:
        return self._lifecycle("POST", flow_run_id, "retry")

    def _lifecycle(
        self,
        method: str,
        flow_run_id: str,
        action: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        encoded = quote(flow_run_id, safe="")
        path = f"/api/flow-runs/{encoded}/{action}"
        if method != "POST":
            raise ValueError(f"unsupported method: {method}")
        response = self._session.post(path, json=json_body)
        if response.status_code == 404:
            raise LookupError(f"flow run not found: {flow_run_id}")
        if response.status_code in {400, 409, 422}:
            try:
                payload = response.json()
            except Exception:
                payload = {"detail": "request failed"}
            raise RuntimeError(json.dumps(payload, default=str))
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"ok": True}


def _client_from_args(args: argparse.Namespace) -> FlowRunClient:
    return FlowRunClient(args.api_url or _default_api_url())


def _handle_errors(exc: Exception) -> int:
    if isinstance(exc, LookupError):
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if isinstance(exc, RuntimeError):
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Error: {exc}", file=sys.stderr)
    return 1


def cmd_flow_run_ls(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(
            client.list_flow_runs(
                state=args.state,
                limit=args.limit,
                cursor=args.cursor,
                include_archived=args.include_archived,
            )
        )
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def cmd_flow_run_inspect(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.get_flow_run(args.flow_run_id))
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def cmd_flow_run_task_runs(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(
            client.list_task_runs(
                args.flow_run_id, limit=args.limit, cursor=args.cursor
            )
        )
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def cmd_flow_run_logs(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(
            client.list_logs(
                args.flow_run_id,
                task_run_id=args.task_run_id,
                level=args.level,
                limit=args.limit,
                cursor=args.cursor,
            )
        )
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def cmd_flow_run_events(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(
            client.list_events(args.flow_run_id, limit=args.limit, cursor=args.cursor)
        )
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def cmd_flow_run_dag(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.get_dag(args.flow_run_id, mode=args.mode))
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def cmd_flow_run_cancel(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.cancel(args.flow_run_id))
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def cmd_flow_run_pause(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.pause(args.flow_run_id, mode=args.mode))
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def cmd_flow_run_resume(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.resume(args.flow_run_id))
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def cmd_flow_run_retry(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        _print_json(client.retry(args.flow_run_id))
        return 0
    except Exception as exc:
        return _handle_errors(exc)
    finally:
        client.close()


def add_flow_run_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    api_parent = argparse.ArgumentParser(add_help=False)
    api_parent.add_argument(
        "--api-url",
        default=None,
        help=f"API base URL (default: IRONFLOW_API_URL or {DEFAULT_API_URL}).",
    )
    flow_run_parser = subparsers.add_parser(
        "flow-run",
        help="Inspect and control flow runs (JSON output).",
    )
    flow_run_sub = flow_run_parser.add_subparsers(
        dest="flow_run_command", required=True
    )

    ls_parser = flow_run_sub.add_parser(
        "ls",
        parents=[api_parent],
        help="List flow runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow flow-run ls",
                "ironflow flow-run ls --state FAILED --limit 20",
            ]
        ),
    )
    ls_parser.add_argument("--state", default=None, help="Filter by state.")
    ls_parser.add_argument("--limit", type=int, default=50, help="Page size.")
    ls_parser.add_argument("--cursor", default=None, help="Pagination cursor.")
    ls_parser.add_argument(
        "--include-archived",
        action="store_true",
        help="Include archived flow runs.",
    )
    ls_parser.set_defaults(func=cmd_flow_run_ls)

    inspect_parser = flow_run_sub.add_parser(
        "inspect",
        parents=[api_parent],
        help="Show one flow run by id.",
    )
    inspect_parser.add_argument("flow_run_id", help="Flow run UUID.")
    inspect_parser.set_defaults(func=cmd_flow_run_inspect)

    task_runs_parser = flow_run_sub.add_parser(
        "task-runs",
        parents=[api_parent],
        help="List task runs for a flow run.",
    )
    task_runs_parser.add_argument("flow_run_id", help="Flow run UUID.")
    task_runs_parser.add_argument("--limit", type=int, default=200)
    task_runs_parser.add_argument("--cursor", default=None)
    task_runs_parser.set_defaults(func=cmd_flow_run_task_runs)

    logs_parser = flow_run_sub.add_parser(
        "logs",
        parents=[api_parent],
        help="List logs for a flow run.",
    )
    logs_parser.add_argument("flow_run_id", help="Flow run UUID.")
    logs_parser.add_argument("--task-run-id", default=None)
    logs_parser.add_argument("--level", default=None)
    logs_parser.add_argument("--limit", type=int, default=500)
    logs_parser.add_argument("--cursor", default=None)
    logs_parser.set_defaults(func=cmd_flow_run_logs)

    events_parser = flow_run_sub.add_parser(
        "events",
        parents=[api_parent],
        help="List events for a flow run.",
    )
    events_parser.add_argument("flow_run_id", help="Flow run UUID.")
    events_parser.add_argument("--limit", type=int, default=500)
    events_parser.add_argument("--cursor", default=None)
    events_parser.set_defaults(func=cmd_flow_run_events)

    dag_parser = flow_run_sub.add_parser(
        "dag",
        parents=[api_parent],
        help="Get the flow-run DAG (logical or expanded).",
    )
    dag_parser.add_argument("flow_run_id", help="Flow run UUID.")
    dag_parser.add_argument(
        "--mode",
        default="logical",
        choices=["logical", "expanded"],
        help="DAG mode (default: logical).",
    )
    dag_parser.set_defaults(func=cmd_flow_run_dag)

    cancel_parser = flow_run_sub.add_parser(
        "cancel",
        parents=[api_parent],
        help="Cancel a flow run (terminate semantics).",
    )
    cancel_parser.add_argument("flow_run_id", help="Flow run UUID.")
    cancel_parser.set_defaults(func=cmd_flow_run_cancel)

    pause_parser = flow_run_sub.add_parser(
        "pause",
        parents=[api_parent],
        help="Pause a flow run (requires --mode drain|terminate).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(
            [
                "ironflow flow-run pause <id> --mode drain",
                "ironflow flow-run pause <id> --mode terminate",
            ]
        ),
    )
    pause_parser.add_argument("flow_run_id", help="Flow run UUID.")
    pause_parser.add_argument(
        "--mode",
        required=True,
        choices=["drain", "terminate"],
        help="Pause mode (required).",
    )
    pause_parser.set_defaults(func=cmd_flow_run_pause)

    resume_parser = flow_run_sub.add_parser(
        "resume",
        parents=[api_parent],
        help="Resume an operator-paused flow run.",
    )
    resume_parser.add_argument("flow_run_id", help="Flow run UUID.")
    resume_parser.set_defaults(func=cmd_flow_run_resume)

    retry_parser = flow_run_sub.add_parser(
        "retry",
        parents=[api_parent],
        help="Retry a deployment-backed flow run.",
    )
    retry_parser.add_argument("flow_run_id", help="Flow run UUID.")
    retry_parser.set_defaults(func=cmd_flow_run_retry)
