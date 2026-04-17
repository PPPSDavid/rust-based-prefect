# IronFlow Deterministic Performance Matrix

- Generated: `2026-04-17T03:56:57.319890+00:00`
- Git SHA: `5173d32adb89f44dcdec668feea652020fadc1bb`
- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.13.12`
- Raw JSON: `docs/perf_candidate_post_boundconn_threshold.json`

## Recipe Results

| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | 1.853 | 72.19 | 1.951 | 1.900 | 4.363 | 0.311 | 1.418 | 1.044 | 0 |
| medium_wide_heavy_write_warm | 3.621 | 43.20 | 1.856 | 1.935 | 4.325 | 0.317 | 1.334 | 2.041 | 0 |
| small_narrow_few_write_cold | 0.238 | 152.40 | 5.974 | 1.583 | 3.858 | 0.269 | 3.560 | 0.106 | 0 |

## Anti-Flake Controls

- Deterministic random seed per recipe/iteration.
- Fixed recipe catalog with bounded sizes.
- Warmup iterations are excluded from aggregates.
- Metrics use medians/p95/p99 across multiple repetitions.
