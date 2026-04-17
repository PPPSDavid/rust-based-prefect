# Performance Regression Report

- Baseline: `docs/perf_baseline_pre_next_step.json`
- Candidate: `docs/perf_candidate_post_flow_batch_tx.json`
- Status: `FAIL`

## Regressions

| Recipe | Metric | Baseline | Candidate | Delta % | Threshold % |
| --- | --- | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | latency_ms.create_flow.p95 | 1.717 | 2.997 | 74.59% | 10.00% |
| medium_narrow_heavy_mixed_warm | latency_ms.set_flow_state.p95 | 4.472 | 5.863 | 31.08% | 10.00% |
| medium_narrow_heavy_mixed_warm | throughput.transitions_per_sec.median | 73.235 | 59.622 | -18.59% | 10.00% |
| medium_narrow_heavy_mixed_warm | wall_clock_seconds.p95 | 1.692 | 3.409 | 101.43% | 10.00% |
| medium_wide_heavy_write_warm | latency_ms.create_flow.p95 | 1.712 | 2.212 | 29.25% | 10.00% |
| medium_wide_heavy_write_warm | latency_ms.set_flow_state.p95 | 4.384 | 4.823 | 10.01% | 10.00% |
| medium_wide_heavy_write_warm | throughput.transitions_per_sec.median | 44.209 | 36.643 | -17.11% | 10.00% |
| medium_wide_heavy_write_warm | wall_clock_seconds.p95 | 3.413 | 4.666 | 36.72% | 10.00% |

## CI Summary

- compared metrics: `12`
- regressions: `8`
