# Airtight concurrent-state harness

**Status:** Landed with P3.2e / P4.0 — keep adding cases; do not treat `perf_matrix` as this gate.  
**North star:** overlapping tasks and parallel flow runs never produce illegal states, double claims, or leaked leases.  
**Tests:** `pytest -m airtight` (`python-shim/tests/test_airtight_concurrency.py` plus `test_race_and_load.py`).

## What this is not

- Not a latency/throughput gate (`benchmarks/perf_matrix.py`).
- Not permission to remove or per-flow-shard the control-plane write lock.
- Not HA / multi-services leader election (later plan).

## Invariants

| Invariant | Test |
| --- | --- |
| Duplicate transition token → one `applied` | `test_duplicate_token_race`, `test_duplicate_tokens_across_many_flow_runs` |
| N parallel `@flow` + concurrent `submit` → legal terminals | `test_parallel_distinct_flow_runs_legal_terminals` |
| `wait_all` FAILED child cannot COMPLETE the flow | `test_wait_all_failed_submit_cannot_complete_under_overlap` |
| `detach=True` excluded from aggregation | `test_wait_all_detach_failed_stays_completed` |
| Late `task_completed` after cancel stays `CANCELLED` | `test_late_completed_after_cancel_stays_cancelled` |
| Concurrent deployment claims → one winner | `test_concurrent_claims_exactly_one_winner` |
| `concurrency()` records `holder_id` from run context | `test_concurrency_context_binds_task_holder` |
| Cancel / terminate-pause frees GCL holder leases | `test_cancel_releases_gcl_leases_by_holder`, `test_terminate_pause_releases_gcl_leases` |

Postgres Compose should keep the same semantics; do not add SQLite-only shortcuts in these paths.

## Implementation notes

- Rust `gcl_release_by_holders` + Python `concurrency_store.release_by_holders`.
- `concurrency(...)` records `holder_id` from `get_run_context()` when the caller omits it.
- `cancel_flow_run` / terminate pause call `_release_gcl_holders_for_flow`.
