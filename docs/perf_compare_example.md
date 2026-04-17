# Performance Regression Report

- Baseline: `docs/perf_baseline_example.json`
- Candidate: `docs/perf_candidate_example.json`
- Status: `FAIL`

## Regressions

| Recipe | Metric | Baseline | Candidate | Delta % | Threshold % |
| --- | --- | ---: | ---: | ---: | ---: |
| small_narrow_few_write_cold | latency_ms.create_flow.p95 | 2.000 | 2.450 | 22.50% | 10.00% |
| small_narrow_few_write_cold | latency_ms.set_flow_state.p95 | 1.700 | 1.950 | 14.71% | 10.00% |
| small_narrow_few_write_cold | throughput.transitions_per_sec.median | 240.000 | 205.000 | -14.58% | 10.00% |
| small_narrow_few_write_cold | wall_clock_seconds.p95 | 1.400 | 1.620 | 15.71% | 10.00% |

## CI Summary

- compared metrics: `4`
- regressions: `4`
