#!/bin/sh
set -eu

if [ -z "${FLOWOXIDE_HISTORY_PATH:-}" ]; then
  export FLOWOXIDE_HISTORY_PATH=/data/flowoxide_history.jsonl
fi

mkdir -p "$(dirname "$FLOWOXIDE_HISTORY_PATH")"

exec "$@"
