# Docker images

Official container images complement the **`ironflow-prefect-compat`** PyPI package.

## PyPI vs container

| Channel | What you get | Typical use |
| --- | --- | --- |
| **PyPI** (`pip install ironflow-prefect-compat`) | Python library, `ironflow` CLI, bundled Rust engine wheel | Author flows, embed in apps, CI scripts, custom server commands |
| **Container** (`ghcr.io/<org>/ironflow-server`) | Ready-to-run API server: uvicorn + FastAPI + pinned wheel + `/data` volume defaults | Self-hosted control plane, compose/Kubernetes, no local Python setup |

The image **installs the same PyPI wheel** (same `VERSION` tag). It adds server runtime dependencies (`fastapi`, `uvicorn`), an entrypoint, healthcheck, and default env vars. You do not need a separate “Docker edition” of the engine.

## Tags (proposed)

| Tag | Meaning |
| --- | --- |
| `ghcr.io/<org>/ironflow-server:0.1.2` | Immutable release (matches `VERSION` / PyPI) |
| `ghcr.io/<org>/ironflow-server:0.1` | Patch-line float |
| `ghcr.io/<org>/ironflow-server:latest` | Latest release (avoid in production) |

Worker and UI images are planned under Tier B5 of `docs/plans/self-hosted-docker-auth.md`.

## Build

From repository root:

```bash
# Release-style (pulls wheel from PyPI)
mkdir -p dist/wheels
docker build -f deploy/docker/Dockerfile.server \
  --build-arg INSTALL_MODE=pypi \
  --build-arg IRONFLOW_VERSION=0.1.2 \
  -t ironflow-server:0.1.2 .

# Branch validation (wheel from current checkout)
cd python-shim && python -m build --wheel --outdir ../dist/wheels && cd ..
docker build -f deploy/docker/Dockerfile.server \
  --build-arg INSTALL_MODE=local \
  -t ironflow-server:local .
```

## Publish (maintainers)

1. Release **`ironflow-prefect-compat`** to PyPI first (existing workflow).
2. Build and push the server image with the **same version**:
   - Registry: **GHCR** `ghcr.io/PPPSDavid/ironflow-server` (recommended; pairs with GitHub releases)
   - Optional mirror: Docker Hub `pppsdavid/ironflow-server`
3. Attach `docker pull …` to the GitHub Release notes.

A `workflow_dispatch` GHCR workflow is planned; until then, maintainers push manually:

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u USERNAME --password-stdin
docker tag ironflow-server:0.1.2 ghcr.io/pppsdavid/ironflow-server:0.1.2
docker push ghcr.io/pppsdavid/ironflow-server:0.1.2
```

## Smoke test

```bash
bash scripts/docker_server_smoke.sh
```
