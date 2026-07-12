from __future__ import annotations

import base64
import os
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


def server_api_auth_string() -> str | None:
    raw = os.getenv("IRONFLOW_SERVER_API_AUTH_STRING")
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def api_client_auth_string() -> str | None:
    raw = os.getenv("IRONFLOW_API_AUTH_STRING")
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def basic_authorization_header(auth_string: str | None = None) -> dict[str, str]:
    value = auth_string if auth_string is not None else api_client_auth_string()
    if not value:
        return {}
    token = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def merge_auth_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(headers or {})
    merged.update(basic_authorization_header())
    return merged


def _decode_basic_credentials(header: str) -> str | None:
    prefix = "Basic "
    if not header.startswith(prefix):
        return None
    try:
        return base64.b64decode(header[len(prefix) :].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _unauthorized() -> Response:
    return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        auth_string_provider: Callable[[], str | None] | None = None,
    ) -> None:
        super().__init__(app)
        self._auth_string_provider = auth_string_provider or server_api_auth_string

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        auth_string = self._auth_string_provider()
        if auth_string is None:
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in {"/health", "/health/"}:
            return await call_next(request)
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        header = request.headers.get("Authorization")
        if header is None:
            return _unauthorized()
        decoded = _decode_basic_credentials(header)
        if decoded != auth_string:
            return _unauthorized()
        return await call_next(request)
