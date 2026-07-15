# Docker images

Official container images complement the **`ironflow-prefect-compat`** PyPI package.

## PyPI vs container

| Channel | What you get | Typical use |
| --- | --- | --- |
| **PyPI** (`pip install ironflow-prefect-compat`) | Python library, `ironflow` CLI, bundled Rust engine wheel | Author flows, embed in apps, CI scripts, custom server commands |
| **Container** (`ghcr.io/<org>/ironflow-server`) | Ready-to-run API / services / HTTP worker images | Self-hosted control plane + compose |

Images install the **same PyPI wheel** (same `VERSION` tag). Server/services images also install `psycopg[binary]` for Postgres.

## Images

| Dockerfile | Role | Default `CMD` |
| --- | --- | --- |
| `Dockerfile.server` | API (uvicorn) | `uvicorn … --host 0.0.0.0 --port 8000` |
| `Dockerfile.services` | Background scheduler | `ironflow server services start` |
| `Dockerfile.worker` | HTTP worker | `ironflow worker start --worker-mode http` |

## Tags (proposed)

| Tag | Meaning |
| --- | --- |
| `ghcr.io/<org>/ironflow-server:0.2.0` | Immutable release (matches `VERSION` / PyPI) |
| `ghcr.io/<org>/ironflow-server:0.2` | Patch-line float |
| `ghcr.io/<org>/ironflow-server:latest` | Latest release (avoid in production) |

Same versioning for `ironflow-services` / `ironflow-worker` when published.

## Compose

See `compose.yml` and [docs/how-to/docker-compose.md](../../docs/how-to/docker-compose.md).

```bash
mkdir -p dist/wheels
python -m build --wheel --outdir dist/wheels --directory python-shim
docker compose -f deploy/docker/compose.yml up --build
```

## Build (server example)

```bash
# Branch validation (wheel from current checkout)
mkdir -p dist/wheels
python -m build --wheel --outdir dist/wheels --directory python-shim
docker build -f deploy/docker/Dockerfile.server \
  --build-arg INSTALL_MODE=local \
  -t ironflow-server:local .
```

## Publish (maintainers)

1. Release **`ironflow-prefect-compat`** to PyPI first.
2. Build and push images with the **same version** to GHCR (`ghcr.io/PPPSDavid/ironflow-{server,services,worker}`).
3. Attach `docker pull …` to the GitHub Release notes.

Automated GHCR publish may be added as a follow-up workflow; until then, maintainers push manually.

## Smoke tests

```bash
bash scripts/docker_server_smoke.sh      # Tier A single container
bash scripts/docker_compose_smoke.sh     # Tier B3/B5 compose stack
```
