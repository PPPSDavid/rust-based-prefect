"""Thin HTTP GET client for the IronFlow control-plane API."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from prefect_compat.auth import merge_auth_headers

DEFAULT_API_URL = "http://127.0.0.1:8000"


def default_api_url() -> str:
    return os.getenv("IRONFLOW_API_URL", DEFAULT_API_URL).rstrip("/")


class IronFlowHttp:
    """Minimal JSON GET/POST helper (POST unused in Phase 1 read-only tools)."""

    def __init__(
        self,
        base_url: str | None = None,
        session: Any | None = None,
    ) -> None:
        self.base_url = (base_url or default_api_url()).rstrip("/")
        self._session = session
        self._owns_session = False
        if session is None:
            try:
                import httpx

                self._session = httpx.Client(
                    base_url=self.base_url,
                    headers=merge_auth_headers(),
                )
                self._owns_session = True
            except ImportError:
                self._session = None

    def close(self) -> None:
        if self._owns_session and self._session is not None:
            close = getattr(self._session, "close", None)
            if callable(close):
                close()

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if not path.startswith("/"):
            path = f"/{path}"
        if params:
            filtered = {
                key: str(value)
                for key, value in params.items()
                if value is not None and value != ""
            }
            if filtered:
                path = f"{path}?{urlencode(filtered, doseq=True)}"

        if self._session is not None:
            response = self._session.get(path)
            status = int(response.status_code)
            try:
                payload = response.json()
            except Exception:
                payload = {"raw": getattr(response, "text", "")}
            if status >= 400:
                raise RuntimeError(
                    json.dumps(
                        {"status": status, "error": payload},
                        default=str,
                    )
                )
            return payload

        url = f"{self.base_url}{path}"
        request = Request(url, headers=merge_auth_headers(), method="GET")
        try:
            with urlopen(request) as response:
                body = response.read()
                return json.loads(body.decode("utf-8")) if body else None
        except HTTPError as exc:
            body = exc.read() if exc.fp is not None else b""
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                payload = {"raw": body.decode("utf-8", errors="replace")}
            raise RuntimeError(
                json.dumps({"status": exc.code, "error": payload}, default=str)
            ) from exc
