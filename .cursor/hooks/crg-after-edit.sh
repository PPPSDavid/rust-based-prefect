#!/usr/bin/env bash
# Cursor afterFileEdit: incremental graph refresh (best-effort, never block edits).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 0
# Consume Cursor hook stdin JSON if present.
cat >/dev/null 2>&1 || true
if ! command -v python3 >/dev/null 2>&1; then
  echo '{}'
  exit 0
fi
if ! python3 -c "import code_review_graph" >/dev/null 2>&1; then
  echo '{}'
  exit 0
fi
python3 -m code_review_graph update >/dev/null 2>&1 || true
echo '{}'
exit 0
