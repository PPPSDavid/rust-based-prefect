#!/usr/bin/env bash
# Cursor sessionStart: surface graph health to the agent session.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 0
cat >/dev/null 2>&1 || true
msg="code-review-graph: not installed (run bash scripts/setup_code_review_graph.sh)"
if command -v python3 >/dev/null 2>&1 && python3 -c "import code_review_graph" >/dev/null 2>&1; then
  if [[ -d "$ROOT/.code-review-graph" ]]; then
    status="$(python3 -m code_review_graph status 2>/dev/null | tr '\n' ' ' | head -c 240 || true)"
    msg="code-review-graph ready: ${status}"
  else
    msg="code-review-graph installed but graph DB missing — run bash scripts/setup_code_review_graph.sh"
  fi
fi
# Cursor hooks protocol: emit JSON on stdout.
python3 -c 'import json,sys; print(json.dumps({"additional_context": sys.argv[1]}))' "$msg" 2>/dev/null || echo '{}'
exit 0
