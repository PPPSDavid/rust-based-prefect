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

### Do we need embeddings?

**Not for the main agent workflows.** Upstream treats embeddings as
**optional** ([USAGE § semantic search](https://github.com/tirth8205/code-review-graph/blob/main/docs/USAGE.md),
[FAQ: Isn't this just RAG?](https://github.com/tirth8205/code-review-graph/blob/main/docs/FAQ.md)):
vectors only help pick a *starting node* for hybrid search; blast radius,
`query_graph_tool`, architecture, and `detect_changes_tool` use structural
AST edges and work with `embeddings_count=0`.

Without embeddings, `semantic_search_nodes_tool` falls back to FTS5/keyword:

| Query style | Without embeddings (observed here) |
| --- | --- |
| Identifiers / symbol-ish (`TransitionHookSpec`) | Good hits |
| Token overlap (`deployment schedule cron rust`) | Often OK |
| Natural-language conceptual (`how does flow cancellation work`) | Often **0 hits** |

For better conceptual search on Cloud, optionally install embeddings and embed
once after build (heavier: torch + model download; slows session setup):

```bash
python3 -m pip install --user --break-system-packages 'code-review-graph[embeddings]'
python3 -m code_review_graph build
# then via MCP/CLI: embed_graph / embed_graph_tool
# default local model: all-MiniLM-L6-v2 (override with CRG_EMBEDDING_MODEL)
```

Windows desktop already supported a richer path (conda + local embedding model);
keep that with `CRG_MCP_USE_CONDA=1` and an explicit `CRG_MCP_CONDA_ENV` if you
want GPU embeddings there.

Verify with real stdio MCP tool calls (not just `status`):

```bash
python3 scripts/verify_code_review_graph.py
```

Proven on Cursor Cloud: full graph build for this repo is typically under 2s
without the optional `[embeddings]` extra. Setup runs verification by default
(`CRG_SKIP_VERIFY=1` to skip).

### Make it default every Cloud session

This repo commits `.cursor/environment.json` → `bash .cursor/cloud-install.sh`,
which calls `scripts/setup_code_review_graph.sh` on every agent boot (after the
branch checkout). That is the durable path — higher priority than a personal
dashboard environment per Cursor’s resolution order.

After changing `.cursor/mcp.json` or `environment.json`, start a **new** agent
session so Cursor reloads MCP / picks up install.

Windows desktop (optional embeddings)

Use the legacy PowerShell launcher or set explicit env vars (no repo-default
conda env name — set your own):

```text
CRG_MCP_USE_CONDA=1
CRG_MCP_CONDA_ENV=<your-env-with-code-review-graph[embeddings]>
CRG_APPLY_ST_CACHE_PATCH=1   # optional
CRG_EMBEDDING_MODEL=<model>  # optional; provider default otherwise
```

Or point Cursor MCP `command` at `powershell.exe` + `crg_mcp_serve.ps1` (requires
`CRG_MCP_CONDA_ENV`).

## Hooks

Committed `.cursor/hooks.json` only runs **sessionStart** (graph status) and a
**git commit** reminder for dirty `perf_matrix` docs. Per-edit graph refresh is
intentionally **not** enabled by default (too noisy). Optional script:
`.cursor/hooks/crg-after-edit.sh` — wire it locally if you want.
## Files

| File | Role |
| --- | --- |
| `crg_mcp_serve.py` | Cross-platform MCP entry (default) |
| `crg_mcp_serve.ps1` | Windows conda launcher (legacy) |
| `crg_st_model_cache.py` | Optional SentenceTransformer instance cache |
| `install_crg_st_cache_patch.ps1` | Windows helper to install the cache patch |
| `../../scripts/setup_code_review_graph.sh` | Install + build for Cloud/Linux |
| `../../requirements-agent.txt` | Pinned agent tooling deps |
