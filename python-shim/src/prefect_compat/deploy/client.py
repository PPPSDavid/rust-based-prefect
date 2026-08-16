from __future__ import annotations

import json
from email.message import Message
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ..auth import merge_auth_headers
from .spec import DeploymentSpec

DEFAULT_WORK_POOL_NAME = "default-process-pool"


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

    def patch(self, path: str, json: dict[str, Any] | None = None) -> _UrllibResponse:
        return self._request("PATCH", path, json_body=json)

    def delete(self, path: str) -> _UrllibResponse:
        return self._request("DELETE", path)


class DeployClient:
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

    def __enter__(self) -> DeployClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def ensure_work_pool(self, name: str, pool_type: str = "process") -> dict[str, Any]:
        response = self._session.post(
            "/api/work-pools", json={"name": name, "type": pool_type}
        )
        response.raise_for_status()
        return response.json()

    def _find_work_pool_by_name(self, name: str) -> dict[str, Any] | None:
        response = self._session.get("/api/work-pools")
        response.raise_for_status()
        for item in response.json().get("items", []):
            if item.get("name") == name:
                return item
        return None

    def find_deployment_by_name(self, name: str) -> dict[str, Any] | None:
        encoded = quote(name, safe="")
        response = self._session.get(f"/api/deployments/by-name/{encoded}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def upsert_deployment(
        self, spec: DeploymentSpec, dry_run: bool = False
    ) -> dict[str, Any]:
        pool_name = spec.work_pool_name or DEFAULT_WORK_POOL_NAME
        existing = self.find_deployment_by_name(spec.name)

        if dry_run:
            pool = self._find_work_pool_by_name(pool_name)
            action = "update" if existing is not None else "create"
            body: dict[str, Any] | None = None
            if pool is not None:
                body = spec.to_api_body(pool["id"])
            return {
                "action": action,
                "dry_run": True,
                "deployment_id": existing["id"] if existing is not None else None,
                "body": body,
            }

        pool = self.ensure_work_pool(pool_name)
        body = spec.to_api_body(pool["id"])

        if existing is not None:
            patch_body = {
                key: value
                for key, value in body.items()
                if key not in {"name", "flow_name"}
            }
            response = self._session.patch(
                f"/api/deployments/{existing['id']}", json=patch_body
            )
            response.raise_for_status()
            return {"action": "update", "dry_run": False, "deployment": response.json()}

        response = self._session.post("/api/deployments", json=body)
        response.raise_for_status()
        return {"action": "create", "dry_run": False, "deployment": response.json()}
