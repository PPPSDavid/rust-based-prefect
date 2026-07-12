# Dev tooling: code-review-graph (CRG)

Local-first knowledge graph used by agents via MCP. Upstream:
https://github.com/tirth8205/code-review-graph

## Cloud / Linux (default)

```bash
bash scripts/setup_code_review_graph.sh
```

That installs `requirements-agent.txt` (core CRG, no torch embeddings) and
builds `.code-review-graph/` (gitignored). MCP is launched by:

```text
python3 tools/dev/crg_mcp_serve.py
```

configured in `.cursor/mcp.json`.

Proven on Cursor Cloud: full graph build for this repo is typically under 2s
without the optional `[embeddings]` extra.

### Make it default every Cloud session

In the Cursor Cloud Environment dashboard for this repo, append to **Install**
and **Update** scripts:

```bash
bash scripts/setup_code_review_graph.sh
```

(alongside the existing `pip install -r requirements-ci.txt`, `npm ci`, and
`cargo build` steps already described in `AGENTS.md`).

After changing `.cursor/mcp.json`, start a **new** agent session so Cursor
reloads MCP servers.

## Windows desktop (optional embeddings)

Historical path: `crg_mcp_serve.ps1` + conda env `sts2-context-coach` with
`code-review-graph[embeddings]`.

To keep that behavior with the new launcher, set in user MCP env or shell:

```text
CRG_MCP_USE_CONDA=1
CRG_MCP_CONDA_ENV=sts2-context-coach
CRG_APPLY_ST_CACHE_PATCH=1
CRG_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
```

Or point Cursor MCP `command` at `powershell.exe` + `crg_mcp_serve.ps1` as before.

## Files

| File | Role |
| --- | --- |
| `crg_mcp_serve.py` | Cross-platform MCP entry (default) |
| `crg_mcp_serve.ps1` | Windows conda launcher (legacy) |
| `crg_st_model_cache.py` | Optional SentenceTransformer instance cache |
| `install_crg_st_cache_patch.ps1` | Windows helper to install the cache patch |
| `../../scripts/setup_code_review_graph.sh` | Install + build for Cloud/Linux |
| `../../requirements-agent.txt` | Pinned agent tooling deps |
