from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from prefect_compat.auth import (
    api_client_auth_string,
    basic_authorization_header,
    server_api_auth_string,
)
from prefect_compat.auth_middleware import BasicAuthMiddleware
from prefect_compat.server import app


def _auth_header(value: str) -> dict[str, str]:
    token = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_basic_authorization_header_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWOXIDE_API_AUTH_STRING", "admin:pass")
    assert basic_authorization_header() == _auth_header("admin:pass")


def test_server_auth_disabled_allows_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOWOXIDE_SERVER_API_AUTH_STRING", raising=False)
    client = TestClient(app)
    response = client.get("/api/deployments")
    assert response.status_code == 200


def test_server_auth_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWOXIDE_SERVER_API_AUTH_STRING", "admin:pass")
    client = TestClient(app)

    unauthorized = client.get("/api/deployments")
    assert unauthorized.status_code == 401
    assert unauthorized.headers.get("www-authenticate") == "Basic"

    authorized = client.get("/api/deployments", headers=_auth_header("admin:pass"))
    assert authorized.status_code == 200


def test_server_auth_exempts_health(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWOXIDE_SERVER_API_AUTH_STRING", "admin:pass")
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_server_auth_does_not_require_non_api_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLOWOXIDE_SERVER_API_AUTH_STRING", "admin:pass")
    client = TestClient(app)
    response = client.get("/history/summary")
    assert response.status_code == 200


def test_server_auth_rejects_wrong_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWOXIDE_SERVER_API_AUTH_STRING", "admin:pass")
    client = TestClient(app)
    response = client.get("/api/deployments", headers=_auth_header("wrong:creds"))
    assert response.status_code == 401


def test_server_auth_provider_callable() -> None:
    middleware = BasicAuthMiddleware(app, auth_string_provider=lambda: "admin:pass")
    assert middleware._auth_string_provider() == "admin:pass"


def test_empty_auth_env_vars_are_treated_as_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLOWOXIDE_SERVER_API_AUTH_STRING", "   ")
    monkeypatch.setenv("FLOWOXIDE_API_AUTH_STRING", "")
    assert server_api_auth_string() is None
    assert api_client_auth_string() is None
    assert basic_authorization_header() == {}
