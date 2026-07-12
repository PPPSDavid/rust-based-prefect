#!/bin/sh
set -eu

if [ -z "${IRONFLOW_HISTORY_PATH:-}" ]; then
  export IRONFLOW_HISTORY_PATH=/data/ironflow_history.jsonl
fi

mkdir -p "$(dirname "$IRONFLOW_HISTORY_PATH")"

exec "$@"
