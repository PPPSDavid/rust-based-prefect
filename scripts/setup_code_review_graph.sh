#!/usr/bin/env bash
# Install code-review-graph and build (or refresh) the local knowledge graph.
#
# Intended for Cursor Cloud Environment Install/Update scripts and local
# onboarding. Safe to re-run: pip is idempotent; build becomes incremental
# update when .code-review-graph/ already exists.
#
# Usage (from repo root):
#   bash scripts/setup_code_review_graph.sh
#   bash scripts/setup_code_review_graph.sh --skip-build
#
# Cloud dashboard snippet (append to Update / Install):
#   bash scripts/setup_code_review_graph.sh

set -euo pipefail

SKIP_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
  elif command -v python >/dev/null 2>&1; then
    PYTHON=python
  else
    echo "error: python3/python not found" >&2
    exit 1
  fi
fi

PIP_FLAGS=(--upgrade)
# Match Cursor Cloud / PEP 668 hosts used by this repo's AGENTS.md notes.
if "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  if "$PYTHON" -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
    PIP_FLAGS+=(--break-system-packages)
  fi
fi
# Prefer user site on shared images (same pattern as requirements-ci install).
if [[ "$(uname -s)" != "MINGW"* && "$(uname -s)" != "MSYS"* ]]; then
  PIP_FLAGS+=(--user)
fi

REQ="${ROOT}/requirements-agent.txt"
if [[ -f "$REQ" ]]; then
  echo "[crg-setup] Installing from requirements-agent.txt ..."
  "$PYTHON" -m pip install "${PIP_FLAGS[@]}" -r "$REQ"
else
  echo "[crg-setup] Installing code-review-graph (core, no embeddings) ..."
  "$PYTHON" -m pip install "${PIP_FLAGS[@]}" 'code-review-graph>=2.3.6,<3'
fi

export PATH="${HOME}/.local/bin:${PATH}"

if [[ "$SKIP_BUILD" -eq 1 ]]; then
  echo "[crg-setup] Skipping graph build (--skip-build)."
  exit 0
fi

if [[ -d "$ROOT/.code-review-graph" ]]; then
  echo "[crg-setup] Refreshing graph (incremental update) ..."
  "$PYTHON" -m code_review_graph update || "$PYTHON" -m code_review_graph build
else
  echo "[crg-setup] Building graph (first time) ..."
  "$PYTHON" -m code_review_graph build
fi

echo "[crg-setup] Status:"
"$PYTHON" -m code_review_graph status || true
echo "[crg-setup] Done. MCP entry: tools/dev/crg_mcp_serve.py (see .cursor/mcp.json)."
