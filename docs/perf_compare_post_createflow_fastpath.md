# Performance Regression Report

- Baseline: `docs/perf_baseline_pre_next_step.json`
- Candidate: `docs/perf_candidate_post_createflow_fastpath.json`
- Status: `FAIL`

## Regressions

| Recipe | Metric | Baseline | Candidate | Delta % | Threshold % |
| --- | --- | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | latency_ms.create_flow.p95 | 1.717 | 1.992 | 16.03% | 10.00% |
| medium_narrow_heavy_mixed_warm | throughput.transitions_per_sec.median | 73.235 | 64.079 | -12.50% | 10.00% |
| medium_narrow_heavy_mixed_warm | wall_clock_seconds.p95 | 1.692 | 2.020 | 19.36% | 10.00% |
| medium_wide_heavy_write_warm | latency_ms.create_flow.p95 | 1.712 | 1.983 | 15.84% | 10.00% |
| medium_wide_heavy_write_warm | throughput.transitions_per_sec.median | 44.209 | 37.870 | -14.34% | 10.00% |
| medium_wide_heavy_write_warm | wall_clock_seconds.p95 | 3.413 | 4.016 | 17.67% | 10.00% |

## CI Summary

- compared metrics: `12`
- regressions: `6`
