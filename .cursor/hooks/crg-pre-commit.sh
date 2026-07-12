#!/usr/bin/env bash
# Cursor beforeShellExecution matcher for git commit: remind about perf artifacts / CRG.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 0
cat >/dev/null 2>&1 || true
warn=""
if git status --porcelain -- docs/perf_matrix_results.json docs/perf_matrix_summary.md 2>/dev/null | grep -q .; then
  warn="perf_matrix default docs are dirty — revert unless this commit intentionally updates baselines. "
fi
if command -v python3 >/dev/null 2>&1 && python3 -c "import code_review_graph" >/dev/null 2>&1; then
  python3 -m code_review_graph update >/dev/null 2>&1 || true
fi
if [[ -n "$warn" ]]; then
  python3 -c 'import json,sys; print(json.dumps({"permission":"allow","user_message":sys.argv[1]}))' "$warn" 2>/dev/null || echo '{}'
else
  echo '{}'
fi
exit 0
