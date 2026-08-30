# Performance Regression Report

- Baseline: `docs/perf_baseline_pre_next_step.json`
- Candidate: `docs/perf_candidate_post_boundconn_threshold.json`
- Status: `FAIL`

## Regressions

| Recipe | Metric | Baseline | Candidate | Delta % | Threshold % |
| --- | --- | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | latency_ms.create_flow.p95 | 1.717 | 1.951 | 13.63% | 10.00% |

## CI Summary

- compared metrics: `12`
- regressions: `1`
