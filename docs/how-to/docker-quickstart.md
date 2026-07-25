# How to run the FlowOxide server in Docker

Quick path for a **single-container** API server with embedded scheduler and local worker (Tier A). For production-shaped **Postgres + services + HTTP workers**, use **[Docker Compose](docker-compose.md)**. Overview: [Self-hosted server](../SELF_HOSTED_SERVER.md).

**Prefect example to borrow from:** [Run the Prefect server in Docker](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-docker) — same “one container, publish the API port” shape; FlowOxide serves the API on **8000** (Prefect’s UI/API default is **4200**).

## Pull a release image (when published)

```bash
docker pull ghcr.io/pppsdavid/flowoxide-server:0.2.0

docker run -p 8000:8000 \
  -v flowoxide-data:/data \
  -e FLOWOXIDE_HISTORY_PATH=/data/flowoxide_history.jsonl \
  ghcr.io/pppsdavid/flowoxide-server:0.2.0
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
  -t flowoxide-server:local .
```

Or use a **PyPI wheel** inside the image (no local `cargo`):

```bash
mkdir -p dist/wheels
docker build -f deploy/docker/Dockerfile.server \
  --build-arg INSTALL_MODE=pypi \
  --build-arg FLOWOXIDE_VERSION=0.2.0 \
  -t flowoxide-server:0.2.0 .
```

## Run

```bash
docker run -p 8000:8000 \
  -v flowoxide-data:/data \
  -e FLOWOXIDE_HISTORY_PATH=/data/flowoxide_history.jsonl \
  flowoxide-server:local
```

Important container settings (same idea as Prefect’s `--host 0.0.0.0`):

- The image binds **`0.0.0.0:8000`** inside the container.
- Map a host port with `-p 8000:8000`.
- Persist state with a volume on **`/data`** (`FLOWOXIDE_HISTORY_PATH`).

Point clients at the host-mapped URL:

```bash
export FLOWOXIDE_API_URL=http://127.0.0.1:8000
flowoxide deploy --file flowoxide.yaml --all
```

## Optional: basic auth

See [Secure a self-hosted server](secure-self-hosted.md). Example:

```bash
docker run -p 8000:8000 \
  -v flowoxide-data:/data \
  -e FLOWOXIDE_SERVER_API_AUTH_STRING='admin:pass' \
  flowoxide-server:local
```

Clients and CLI:

```bash
export FLOWOXIDE_API_AUTH_STRING='admin:pass'
curl -u admin:pass http://127.0.0.1:8000/api/deployments
```

## Validate end-to-end

```bash
bash scripts/docker_server_smoke.sh
```

## PyPI package vs Docker image

| Install path | Best for |
| --- | --- |
| `pip install flowoxide-prefect-compat` | Flow code, libraries, custom processes |
| `docker pull ghcr.io/.../flowoxide-server` | Running the control-plane API without managing Python/uvicorn |

Both ship the **same wheel version**; the image is an opinionated server runtime. Maintainer notes (PyPI vs GHCR tags) live in `deploy/docker/README.md` in the repository.

## Related

- [Docker Compose](docker-compose.md)
- [Self-hosted server](../SELF_HOSTED_SERVER.md)
- [Environment variables](../reference/env-vars.md)
- [Secure a self-hosted server](secure-self-hosted.md)
