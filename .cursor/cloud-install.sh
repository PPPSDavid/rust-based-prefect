#!/usr/bin/env bash
# Cursor Cloud update/install script (idempotent).
# Invoked via .cursor/environment.json "install" on every new agent VM boot
# after the latest commit is checked out.
#
# Keep this aligned with AGENTS.md → "Cursor Cloud specific instructions".

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[cloud-install] repo=$ROOT"

# Ensure ~/.local/bin is visible for uv and console scripts during this script.
export PATH="${HOME}/.local/bin:${PATH}"

echo "[cloud-install] Ensuring uv ..."
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

echo "[cloud-install] Python deps (uv sync --frozen) ..."
# App/test deps come from the committed uv.lock + [dependency-groups].dev.
# Workspace packages stay on pytest.ini pythonpath; skip native build during sync.
FLOWOXIDE_SKIP_NATIVE_BUILD=1 uv sync --frozen --group dev
export PATH="${ROOT}/.venv/bin:${PATH}"

echo "[cloud-install] Frontend deps ..."
npm --prefix frontend ci

echo "[cloud-install] Rust engine ..."
cargo build --manifest-path rust-engine/Cargo.toml

echo "[cloud-install] code-review-graph (install + build) ..."
# Soft-fail verify: a CRG MCP regression must not brick the whole Cloud boot
# (agents still need pytest/cargo/frontend). Setup still builds the graph.
# CRG remains a separate pip install via requirements-agent.txt.
export CRG_SKIP_VERIFY="${CRG_SKIP_VERIFY:-1}"
bash scripts/setup_code_review_graph.sh
if [[ "${CRG_SKIP_VERIFY}" == "1" ]]; then
  echo "[cloud-install] Running CRG MCP verify (non-blocking) ..."
  if ! python3 scripts/verify_code_review_graph.py; then
    echo "[cloud-install] WARN: CRG verify failed — graph/MCP may be unhealthy; app deps are still installed." >&2
  fi
fi

# Help agents that invoke `python` (docs often say python, Cloud may only have python3).
if ! command -v python >/dev/null 2>&1; then
  if [[ -w /usr/local/bin ]]; then
    ln -sf "$(command -v python3)" /usr/local/bin/python || true
  fi
fi

echo "[cloud-install] Done."
