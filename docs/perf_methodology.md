# Prefect vs IronFlow Performance Methodology

This benchmark compares two engines with the same high-level flow shapes:

- `mapped`: one upstream task plus fan-out mapped tasks
- `chained`: sequential submit chain where each task waits on the previous

Engines benchmarked:

- `ironflow`: in-process compatibility runtime
- `ironflow_http`: same runtime through an HTTP server boundary (`uvicorn` + FastAPI)
- `prefect`: local Prefect server orchestration

## Metrics

- `startup_seconds`
  - IronFlow: in-process control-plane initialization.
  - Prefect: local server startup to health-ready.
- `runtime_seconds`
  - End-to-end flow execution time.
- `transition_events`
  - Estimated comparable transition count from run/task lifecycle.
- `transitions_per_second`
  - `transition_events / runtime_seconds`.

## Notes

- Prefect runs through local server orchestration path and includes orchestration overhead.
- IronFlow in-process is the lower-bound prototype path.
- IronFlow HTTP mode adds network/process overhead and is a closer control-plane comparison shape.
- These results are directional for architecture validation, not a final production benchmark.
- Raw benchmark output is in `docs/perf_comparison.json`.
