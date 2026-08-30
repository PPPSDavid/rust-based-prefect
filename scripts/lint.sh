#!/usr/bin/env bash
# Local lint/format/complexity gates matching CI (python-lint + rust-lint + code_metrics).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if command -v uv >/dev/null 2>&1; then
    UV_RUN=(uv run)
  else
    UV_RUN=()
    PYTHON="${PYTHON:-python3}"
  fi
else
  UV_RUN=()
fi

run_py() {
  if [[ ${#UV_RUN[@]} -gt 0 ]]; then
    "${UV_RUN[@]}" "$@"
  else
    "$PYTHON" -m "$@"
  fi
}

echo "== ruff check =="
run_py ruff check .

echo "== ruff format --check =="
run_py ruff format --check .

echo "== ty check =="
run_py ty check

echo "== code_metrics (file LOC ratchet) =="
if [[ ${#UV_RUN[@]} -gt 0 ]]; then
  uv run python scripts/code_metrics.py
else
  "$PYTHON" scripts/code_metrics.py
fi

echo "== cargo fmt --check =="
cargo fmt --manifest-path rust-engine/Cargo.toml -- --check

echo "== cargo clippy -D warnings =="
cargo clippy --manifest-path rust-engine/Cargo.toml --all-targets -- -D warnings

echo "lint.sh: ok"
