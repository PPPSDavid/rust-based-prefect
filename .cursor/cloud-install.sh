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

echo "[cloud-install] Python deps (requirements-ci.txt) ..."
python3 -m pip install --user --break-system-packages -r requirements-ci.txt

echo "[cloud-install] Frontend deps ..."
npm --prefix frontend ci

echo "[cloud-install] Rust engine ..."
cargo build --manifest-path rust-engine/Cargo.toml

echo "[cloud-install] code-review-graph (install + build + verify) ..."
# Ensure ~/.local/bin is visible for console scripts during this script.
export PATH="${HOME}/.local/bin:${PATH}"
bash scripts/setup_code_review_graph.sh

# Help agents that invoke `python` (docs often say python, Cloud may only have python3).
if ! command -v python >/dev/null 2>&1; then
  if [[ -w /usr/local/bin ]]; then
    ln -sf "$(command -v python3)" /usr/local/bin/python || true
  fi
fi

echo "[cloud-install] Done."
