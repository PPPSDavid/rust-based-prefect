"""HTTP client for IronFlow worker claim protocol (Tier B2)."""

from __future__ import annotations

import json
from email.message import Message
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID

from .auth import merge_auth_headers


class _UrllibResponse:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if not self._body:
            return None
        return json.loads(self._body.decode("utf-8"))

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(
                url="",
                code=self.status_code,
                msg=self._body.decode("utf-8", errors="replace"),
                hdrs=Message(),
                fp=None,
            )


class _UrllibSession:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def _request(
        self, method: str, path: str, json_body: dict[str, Any] | None = None
    ) -> _UrllibResponse:
        url = f"{self._base_url}{path}"
        data = None
        headers = merge_auth_headers()
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request) as response:
                body = response.read()
                return _UrllibResponse(response.status, body)
        except HTTPError as exc:
            body = exc.read() if exc.fp is not None else b""
            return _UrllibResponse(exc.code, body)

    def get(self, path: str) -> _UrllibResponse:
        return self._request("GET", path)

    def post(self, path: str, json: dict[str, Any] | None = None) -> _UrllibResponse:
        return self._request("POST", path, json_body=json)


class WorkerHttpClient:
    """Claim / heartbeat / started / finished against a remote API server."""

    def __init__(
        self, base_url: str = "http://127.0.0.1:8000", session: Any | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session if session is not None else self._default_session()
        self._owns_session = session is None

    def _default_session(self) -> Any:
        try:
            import httpx

            return httpx.Client(
                base_url=self.base_url,
                headers=merge_auth_headers(),
            )
        except ImportError:
            return _UrllibSession(self.base_url)

    def close(self) -> None:
        if self._owns_session and hasattr(self._session, "close"):
            self._session.close()

    def __enter__(self) -> WorkerHttpClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def heartbeat(
        self, worker_name: str, *, work_pool_id: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": worker_name}
        if work_pool_id is not None:
            body["work_pool_id"] = work_pool_id
        response = self._session.post("/api/workers/heartbeat", json=body)
        response.raise_for_status()
        return response.json()

    def claim(
        self,
        worker_name: str,
        *,
        work_pool_id: str | None = None,
        lease_seconds: int = 30,
        wait_ms: int | None = None,
    ) -> dict[str, Any] | None:
        body: dict[str, Any] = {
            "worker_name": worker_name,
            "lease_seconds": lease_seconds,
        }
        if work_pool_id is not None:
            body["work_pool_id"] = work_pool_id
        if wait_ms is not None:
            body["wait_ms"] = wait_ms
        response = self._session.post("/api/workers/claim", json=body)
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    def mark_started(self, deployment_run_id: UUID | str) -> dict[str, Any]:
        response = self._session.post(f"/api/workers/runs/{deployment_run_id}/started")
        response.raise_for_status()
        return response.json()

    def mark_finished(
        self,
        deployment_run_id: UUID | str,
        *,
        status: str,
        flow_run_id: UUID | str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"status": status}
        if flow_run_id is not None:
            body["flow_run_id"] = str(flow_run_id)
        if error is not None:
            body["error"] = error
        response = self._session.post(
            f"/api/workers/runs/{deployment_run_id}/finished", json=body
        )
        response.raise_for_status()
        return response.json()

    def get_deployment_run(
        self, deployment_run_id: UUID | str
    ) -> dict[str, Any] | None:
        response = self._session.get(f"/api/workers/runs/{deployment_run_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def get_deployment(self, deployment_id: UUID | str) -> dict[str, Any] | None:
        response = self._session.get(f"/api/deployments/{deployment_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def get_flow_run(self, flow_run_id: UUID | str) -> dict[str, Any] | None:
        response = self._session.get(f"/api/flow-runs/{flow_run_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
