from __future__ import annotations

from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from .auth import decode_basic_credentials, server_api_auth_string


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
        decoded = decode_basic_credentials(header)
        if decoded != auth_string:
            return _unauthorized()
        return await call_next(request)
