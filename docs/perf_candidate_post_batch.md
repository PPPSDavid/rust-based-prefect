# IronFlow Deterministic Performance Matrix

- Generated: `2026-04-17T02:38:26.894330+00:00`
- Git SHA: `5173d32adb89f44dcdec668feea652020fadc1bb`
- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.13.12`
- Raw JSON: `docs/perf_candidate_post_batch.json`

## Recipe Results

| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | 1.750 | 74.66 | 1.702 | 1.861 | 4.220 | 0.295 | 1.451 | 1.009 | 0 |
| medium_wide_heavy_write_warm | 3.499 | 44.76 | 1.794 | 1.836 | 4.071 | 0.297 | 1.439 | 1.956 | 0 |
| small_narrow_few_write_cold | 0.262 | 123.21 | 7.678 | 2.844 | 5.780 | 0.378 | 4.936 | 0.103 | 0 |

## Anti-Flake Controls

- Deterministic random seed per recipe/iteration.
- Fixed recipe catalog with bounded sizes.
- Warmup iterations are excluded from aggregates.
- Metrics use medians/p95/p99 across multiple repetitions.
