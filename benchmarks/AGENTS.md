# AGENTS — benchmarks

Ownership: `perf_matrix.py`, Prefect vs FlowOxide A/B harnesses, recipe definitions.

Read first: root `AGENTS.md` (**Performance: perf_matrix.py**), `docs/perf_methodology.md`.

## Critical rules

- `perf_matrix.py compare` only accepts prior `run` JSON objects (with `aggregates` + `metadata`).
- Never pass `docs/perf_comparison.json` (array from the A/B script) into `compare`.
- Comparable runs must share `metadata.matrix_compare_key` (else exit `3`).
- Default `run` overwrites tracked `docs/perf_matrix_results.json` / `docs/perf_matrix_summary.md` — revert unless the change intends a baseline update.

## Validate

```bash
python3 -m pytest benchmarks/tests
python3 benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2
```
