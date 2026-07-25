from __future__ import annotations

import base64
import os


def server_api_auth_string() -> str | None:
    raw = os.getenv("FLOWOXIDE_SERVER_API_AUTH_STRING")
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def api_client_auth_string() -> str | None:
    raw = os.getenv("FLOWOXIDE_API_AUTH_STRING")
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


def decode_basic_credentials(header: str) -> str | None:
    prefix = "Basic "
    if not header.startswith(prefix):
        return None
    try:
        return base64.b64decode(header[len(prefix) :].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
