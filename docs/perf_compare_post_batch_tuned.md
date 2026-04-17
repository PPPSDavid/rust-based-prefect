# Performance Regression Report

- Baseline: `docs/perf_baseline_pre_next_step.json`
- Candidate: `docs/perf_candidate_post_batch_tuned.json`
- Status: `FAIL`

## Regressions

| Recipe | Metric | Baseline | Candidate | Delta % | Threshold % |
| --- | --- | ---: | ---: | ---: | ---: |
| small_narrow_few_write_cold | latency_ms.set_flow_state.p95 | 4.250 | 5.731 | 34.84% | 10.00% |

## CI Summary

- compared metrics: `12`
- regressions: `1`
