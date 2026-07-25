#!/usr/bin/env bash
# End-to-end smoke test for deploy/docker/Dockerfile.server (Tier A + C).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE="${FLOWOXIDE_SERVER_IMAGE:-flowoxide-server:local}"
HOST_PORT="${FLOWOXIDE_SMOKE_PORT:-18000}"
BASE_URL="http://127.0.0.1:${HOST_PORT}"
INSTALL_MODE="${INSTALL_MODE:-local}"
CONTAINER=""
VOLUME=""

cleanup() {
  if [ -n "${CONTAINER}" ]; then
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  fi
  if [ -n "${VOLUME}" ]; then
    docker volume rm "${VOLUME}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "==> Building wheel from python-shim (INSTALL_MODE=${INSTALL_MODE})"
PYTHON="${PYTHON:-python3}"
"$PYTHON" -m pip install -q build
mkdir -p dist/wheels
(
  cd python-shim
  "$PYTHON" -m build --wheel --outdir "$ROOT/dist/wheels"
)

echo "==> Building Docker image ${IMAGE}"
docker build -f deploy/docker/Dockerfile.server \
  --build-arg INSTALL_MODE="${INSTALL_MODE}" \
  -t "${IMAGE}" .

wait_for_health() {
  local attempt
  for attempt in $(seq 1 45); do
    if curl -sf "${BASE_URL}/health" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "Server did not become healthy at ${BASE_URL}/health" >&2
  docker logs "${CONTAINER}" >&2 || true
  return 1
}

run_container() {
  local auth_env=()
  if [ -n "${1:-}" ]; then
    auth_env=(-e "FLOWOXIDE_SERVER_API_AUTH_STRING=${1}")
  fi
  VOLUME="flowoxide-smoke-${RANDOM}"
  CONTAINER="flowoxide-server-smoke-${RANDOM}"
  docker volume create "${VOLUME}" >/dev/null
  docker run -d --name "${CONTAINER}" \
    -p "${HOST_PORT}:8000" \
    -v "${VOLUME}:/data" \
    -e FLOWOXIDE_HISTORY_PATH=/data/flowoxide_history.jsonl \
    "${auth_env[@]}" \
    "${IMAGE}" >/dev/null
  wait_for_health
}

echo "==> Smoke: open API without auth"
run_container ""
curl -sf "${BASE_URL}/api/deployments" | "$PYTHON" -m json.tool >/dev/null
curl -sf "${BASE_URL}/health" | "$PYTHON" -m json.tool | grep -q '"status": "ok"'

docker rm -f "${CONTAINER}" >/dev/null
CONTAINER=""
docker volume rm "${VOLUME}" >/dev/null
VOLUME=""

echo "==> Smoke: auth enabled"
run_container "admin:pass"
if curl -sf "${BASE_URL}/api/deployments" >/dev/null 2>&1; then
  echo "Expected /api/deployments to require auth" >&2
  exit 1
fi
curl -sf -u "admin:pass" "${BASE_URL}/api/deployments" | "$PYTHON" -m json.tool >/dev/null
curl -sf "${BASE_URL}/health" | "$PYTHON" -m json.tool | grep -q '"status": "ok"'

echo "==> Smoke: deployment create + trigger with auth"
DEPLOYMENT_ID="$(
  curl -sf -u "admin:pass" -X POST "${BASE_URL}/api/deployments" \
    -H 'Content-Type: application/json' \
    -d '{"name":"docker-smoke","flow_name":"simple_flow","default_parameters":{"n":2}}' \
    | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["id"])'
)"
curl -sf -u "admin:pass" -X POST "${BASE_URL}/api/deployments/${DEPLOYMENT_ID}/run" \
  -H 'Content-Type: application/json' \
  -d '{"parameters":{"n":2}}' | "$PYTHON" -m json.tool >/dev/null

echo "==> Smoke: CLI deploy client with FLOWOXIDE_API_AUTH_STRING"
# Install the same local wheel the image used so host client deps (pydantic) resolve.
WHEEL="$(ls "${ROOT}"/dist/wheels/flowoxide_prefect_compat-*.whl | head -n 1)"
"$PYTHON" -m pip install -q "${WHEEL}"
FLOWOXIDE_API_URL="${BASE_URL}" FLOWOXIDE_API_AUTH_STRING="admin:pass" \
  "$PYTHON" -c "
from prefect_compat.deploy.client import DeployClient
client = DeployClient('${BASE_URL}')
pools = client._session.get('/api/work-pools')
pools.raise_for_status()
print('work_pools', len(pools.json().get('items', [])))
client.close()
"

echo "OK: Docker server smoke passed (${IMAGE})"
