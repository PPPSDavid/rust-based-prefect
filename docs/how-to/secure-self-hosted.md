# How to secure a self-hosted FlowOxide server

FlowOxide self-hosted security follows the **Prefect OSS subset**: optional **HTTP Basic Authentication** on `/api/*` routes. There is **no built-in RBAC**, user accounts, or API keys (those are Prefect Cloud features).

**Prefect example to borrow from:** [Secure a self-hosted Prefect server](https://docs.prefect.io/v3/advanced/security-settings) — same Basic auth string pattern (`SERVER` vs client). FlowOxide does **not** yet ship Prefect’s CSRF toggles; prefer a reverse proxy for TLS and edge auth.

## Basic authentication

Set the same `user:password` string on the server and on every client that calls the API.

| Variable | Where | Purpose |
| --- | --- | --- |
| `FLOWOXIDE_SERVER_API_AUTH_STRING` | API server process / container | Require `Authorization: Basic …` on `/api/*` |
| `FLOWOXIDE_API_AUTH_STRING` | CLI, scripts, workers (HTTP clients) | Send credentials on outbound API requests |

Example:

```bash
export FLOWOXIDE_SERVER_API_AUTH_STRING='admin:pass'
export FLOWOXIDE_API_AUTH_STRING='admin:pass'

python -m uvicorn prefect_compat.server:app --host 127.0.0.1 --port 8000
```

With curl, use standard Basic auth (`-u admin:pass`), which encodes the `admin:pass` credential string.

### Docker

```bash
docker run -p 8000:8000 \
  -e FLOWOXIDE_SERVER_API_AUTH_STRING='admin:pass' \
  -v flowoxide-data:/data \
  flowoxide-server:local
```

Store secrets in a `.env` file or orchestrator secret store — not in source control.

### What is protected

| Path | Auth required when enabled |
| --- | --- |
| `/api/*` | Yes |
| `/health` | No (container healthchecks) |
| `/history/summary`, `/benchmark/run` | No |

## Reverse proxy and OIDC (teams)

For SSO, coarse roles, or TLS termination, place FlowOxide behind a reverse proxy (nginx, Traefik, Caddy) with an OIDC layer (for example oauth2-proxy). FlowOxide does not consume identity headers today — enforcement is at the proxy.

Typical pattern:

1. Terminate TLS at the proxy.
2. Require IdP login before traffic reaches FlowOxide.
3. Optionally restrict write methods (`POST`, `PATCH`, `DELETE`) to an admin group at the proxy.

## What FlowOxide does not provide (OSS)

- Per-user accounts or admin UI
- Role-based access control on deployments or work pools
- Prefect Cloud-style API keys
- Audit log of authenticated principals

See [Compatibility matrix](../compatibility.md) for explicit boundaries.

## Related

- [Docker quickstart](docker-quickstart.md)
- [Environment variables](../reference/env-vars.md)
- [REST API overview](../reference/api.md)
