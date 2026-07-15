# How to run the IronFlow server in Docker

Quick path for a **single-container** API server with embedded scheduler and local worker (Tier A). For production compose (Postgres, split workers), see the [self-hosted server](../SELF_HOSTED_SERVER.md) guide; longer-term roadmap lives in `docs/plans/self-hosted-docker-auth.md` in the repository (not published to the MkDocs site).

**Prefect reference:** [Run the Prefect server in Docker](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-docker)

## Pull a release image (when published)

```bash
docker pull ghcr.io/pppsdavid/ironflow-server:0.2.0

docker run -p 8000:8000 \
  -v ironflow-data:/data \
  -e IRONFLOW_HISTORY_PATH=/data/ironflow_history.jsonl \
  ghcr.io/pppsdavid/ironflow-server:0.2.0
```

Open `http://127.0.0.1:8000/health` — expect `{"status":"ok"}`.

## Build from this repository

```bash
# Build a wheel from the current checkout (needs Rust toolchain for the native engine)
mkdir -p dist/wheels
python -m pip install build
python -m build --wheel --outdir dist/wheels --directory python-shim

docker build -f deploy/docker/Dockerfile.server \
  --build-arg INSTALL_MODE=local \
  -t ironflow-server:local .
```

Or use a **PyPI wheel** inside the image (no local `cargo`):

```bash
mkdir -p dist/wheels
docker build -f deploy/docker/Dockerfile.server \
  --build-arg INSTALL_MODE=pypi \
  --build-arg IRONFLOW_VERSION=0.2.0 \
  -t ironflow-server:0.2.0 .
```

## Run

```bash
docker run -p 8000:8000 \
  -v ironflow-data:/data \
  -e IRONFLOW_HISTORY_PATH=/data/ironflow_history.jsonl \
  ironflow-server:local
```

Important container settings (same idea as Prefect’s `--host 0.0.0.0`):

- The image binds **`0.0.0.0:8000`** inside the container.
- Map a host port with `-p 8000:8000`.
- Persist state with a volume on **`/data`** (`IRONFLOW_HISTORY_PATH`).

Point clients at the host-mapped URL:

```bash
export IRONFLOW_API_URL=http://127.0.0.1:8000
ironflow deploy --file ironflow.yaml --all
```

## Optional: basic auth

See [Secure a self-hosted server](secure-self-hosted.md). Example:

```bash
docker run -p 8000:8000 \
  -v ironflow-data:/data \
  -e IRONFLOW_SERVER_API_AUTH_STRING='admin:pass' \
  ironflow-server:local
```

Clients and CLI:

```bash
export IRONFLOW_API_AUTH_STRING='admin:pass'
curl -u admin:pass http://127.0.0.1:8000/api/deployments
```

## Validate end-to-end

```bash
bash scripts/docker_server_smoke.sh
```

## PyPI package vs Docker image

| Install path | Best for |
| --- | --- |
| `pip install ironflow-prefect-compat` | Flow code, libraries, custom processes |
| `docker pull ghcr.io/.../ironflow-server` | Running the control-plane API without managing Python/uvicorn |

Both ship the **same wheel version**; the image is an opinionated server runtime. Maintainer notes (PyPI vs GHCR tags) live in `deploy/docker/README.md` in the repository.

## Related

- [Docker Compose](docker-compose.md)
- [Self-hosted server](../SELF_HOSTED_SERVER.md)
- [Environment variables](../reference/env-vars.md)
- [Secure a self-hosted server](secure-self-hosted.md)
