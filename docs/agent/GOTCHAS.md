# Agent gotchas

- **CRG MCP missing:** `.cursor/mcp.json` must use the Linux-friendly launcher (`tools/dev/crg_mcp_serve.py`). Old PowerShell-only config fails on Cloud. Run `bash scripts/setup_code_review_graph.sh`, then **new session**.
- **CRG tool names:** MCP ids are `detect_changes_tool`, `query_graph_tool`, etc. Unsuffixed `detect_changes` is **Unknown tool**.
- **False-healthy install:** `status` alone is not enough. Run `python3 scripts/verify_code_review_graph.py` (stdio MCP demo calls). Setup script runs this by default.
- **Stale graph:** after large edits, `code-review-graph build` (or setup script) before trusting `detect_changes_tool` risk scores.
- **CRG embeddings:** optional. Needed mainly for natural-language / “by meaning”
  `semantic_search_nodes_tool`. Structural tools work at `embeddings_count=0`.
  See `tools/dev/README.md` (“Do we need embeddings?”).
- **Vite URL:** UI is `http://localhost:4173` (`::1`), not `127.0.0.1:4173`.
- **perf_matrix artifacts:** `run` overwrites tracked `docs/perf_matrix_*.json/md` — revert unless intentional.
- **Wrong compare input:** `docs/perf_comparison.json` is for the Prefect A/B script, not `perf_matrix.py compare`.
- **ruff + ty:** CI gates on `python -m ruff check .` and `python -m ty check` (see root `pyproject.toml`).
- **Parity claims:** update `COMPATIBILITY.md` + tests; never imply full Prefect parity casually.
- **Hotspots:** treat `server.py`, public `__init__.py`, root `pytest.ini`, Cargo/pyproject lockfiles as single-writer.
- **Cancel/retry:** deployment retry starts a **new** flow run with **resume lineage**. Eligible completed tasks (`None` or `@task(persist_result=True)` JSON payloads) can skip; otherwise tasks recompute. Not full Prefect task-resume / `cache_policy` parity — see `docs/how-to/task-resume-and-persist.md` and MEMORY_BANK.
