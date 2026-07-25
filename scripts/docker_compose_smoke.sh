#!/usr/bin/env bash
# End-to-end smoke for deploy/docker/compose.yml (Tier B3/B5).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="${FLOWOXIDE_COMPOSE_FILE:-deploy/docker/compose.yml}"
export INSTALL_MODE="${INSTALL_MODE:-local}"
export FLOWOXIDE_VERSION="${FLOWOXIDE_VERSION:-0.2.0}"
BASE_URL="${FLOWOXIDE_SMOKE_BASE_URL:-http://127.0.0.1:8000}"
PYTHON="${PYTHON:-python3}"

cleanup() {
  docker compose -f "${COMPOSE_FILE}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> Building wheel from python-shim (INSTALL_MODE=${INSTALL_MODE})"
"$PYTHON" -m pip install -q build
mkdir -p dist/wheels
(
  cd python-shim
  "$PYTHON" -m build --wheel --outdir "$ROOT/dist/wheels"
)

echo "==> docker compose up --build"
docker compose -f "${COMPOSE_FILE}" up -d --build

wait_for_health() {
  local attempt
  for attempt in $(seq 1 90); do
    if curl -sf "${BASE_URL}/health" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  echo "Server did not become healthy at ${BASE_URL}/health" >&2
  docker compose -f "${COMPOSE_FILE}" logs >&2 || true
  return 1
}

wait_for_health

echo "==> Create deployment + trigger run"
DEPLOYMENT_ID="$(
  curl -sf -X POST "${BASE_URL}/api/deployments" \
    -H 'Content-Type: application/json' \
    -d '{"name":"compose-smoke","flow_name":"simple_flow","default_parameters":{"n":2}}' \
    | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["id"])'
)"
curl -sf -X POST "${BASE_URL}/api/deployments/${DEPLOYMENT_ID}/run" \
  -H 'Content-Type: application/json' \
  -d '{"parameters":{"n":2}}' >/dev/null

echo "==> Wait for HTTP worker to finish deployment run"
deadline=$((SECONDS + 120))
status=""
while [ "$SECONDS" -lt "$deadline" ]; do
  status="$(
    curl -sf "${BASE_URL}/api/deployment-runs?limit=20" \
      | "$PYTHON" -c "
import json,sys
items=json.load(sys.stdin).get('items',[])
for it in items:
    if it.get('deployment_id')=='${DEPLOYMENT_ID}':
        print(it.get('status','')); break
"
  )"
  if [ "$status" = "COMPLETED" ] || [ "$status" = "FAILED" ] || [ "$status" = "CANCELLED" ]; then
    break
  fi
  sleep 2
done

if [ "$status" != "COMPLETED" ]; then
  echo "Expected COMPLETED, got '${status}'" >&2
  docker compose -f "${COMPOSE_FILE}" logs worker server services >&2 || true
  exit 1
fi

echo "OK: Docker compose smoke passed (${COMPOSE_FILE})"
