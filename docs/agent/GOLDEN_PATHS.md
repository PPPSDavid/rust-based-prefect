# Golden paths — change X, run these

Use this to avoid guessing validation. Prefer package `AGENTS.md` for ownership.

| If you change… | Run / check |
| --- | --- |
| State transitions / FSM / Rust control plane | `cargo test --manifest-path rust-engine/Cargo.toml` + lite `perf_matrix` if timing-sensitive |
| `@flow` / `@task` / shim runtime / `prefect_compat` | `python3 -m pytest python-shim/tests` (+ related file tests first) |
| Compatibility claims | `COMPATIBILITY.md` + `docs/compatibility_review_workflow.md` + shim/planner tests |
| Static planner / forecast / DAG IR | `python3 -m pytest static-planner/tests` |
| Schedules / deployments / workers | shim tests matching schedule/deploy + optional server smoke |
| `perf_matrix.py` / recipes / thresholds | `python3 -m pytest benchmarks/tests` then `perf_matrix.py run --preset lite …` to `/tmp` |
| Frontend run/DAG UI | `npm --prefix frontend run build` (+ e2e if UX-critical); remember `localhost:4173` |
| Cancel / retry semantics | MEMORY_BANK lifecycle section + API/shim tests; do not claim task-resume parity |
| Agent/MCP tooling | `python3 scripts/verify_code_review_graph.py` |

Default full gate (before declaring done):

```bash
python3 -m pytest python-shim/tests static-planner/tests benchmarks/tests
cargo test --manifest-path rust-engine/Cargo.toml
```
