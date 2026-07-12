---
name: ironflow-crg-setup
description: Install and verify code-review-graph (CRG) for IronFlow agents on Linux/Cloud or desktop. Use when MCP graph tools are missing, after cloning, or when setting up Cursor Cloud Update scripts.
---

# IronFlow — code-review-graph setup

Upstream: https://github.com/tirth8205/code-review-graph

## Default (Cloud / Linux)

```bash
bash scripts/setup_code_review_graph.sh
python3 -m code_review_graph status
```

MCP entry is `.cursor/mcp.json` → `python3 ${workspaceFolder}/tools/dev/crg_mcp_serve.py`.

Core package only (`requirements-agent.txt`). Do not install `[embeddings]` on Cloud unless explicitly requested (heavy torch).

## Make it every-session default

Append to Cursor Cloud Environment **Install** and **Update**:

```bash
bash scripts/setup_code_review_graph.sh
```

MCP config changes require a **new** agent session.

## If graph tools still missing mid-session

1. Confirm package: `python3 -c "import code_review_graph; print(code_review_graph.__version__)"`
2. Confirm DB: `python3 -m code_review_graph status`
3. Fall back to Grep/Glob for this turn; fix setup for the next session.

## Windows embeddings (optional)

See `tools/dev/README.md` (`CRG_MCP_USE_CONDA=1`, legacy `crg_mcp_serve.ps1`).
