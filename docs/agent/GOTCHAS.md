# Agent gotchas

- **CRG MCP missing:** `.cursor/mcp.json` must use the Linux-friendly launcher (`tools/dev/crg_mcp_serve.py`). Old PowerShell-only config fails on Cloud. Run `bash scripts/setup_code_review_graph.sh`, then **new session**.
- **Vite URL:** UI is `http://localhost:4173` (`::1`), not `127.0.0.1:4173`.
- **perf_matrix artifacts:** `run` overwrites tracked `docs/perf_matrix_*.json/md` — revert unless intentional.
- **Wrong compare input:** `docs/perf_comparison.json` is for the Prefect A/B script, not `perf_matrix.py compare`.
- **ruff:** installed but not a CI gate; do not mass-fix style in unrelated PRs.
- **Parity claims:** update `COMPATIBILITY.md` + tests; never imply full Prefect parity casually.
- **Hotspots:** treat `server.py`, public `__init__.py`, root `pytest.ini`, Cargo/pyproject lockfiles as single-writer.
- **Cancel/retry:** retry re-runs the full flow today; task-level resume is a known gap (see MEMORY_BANK).
