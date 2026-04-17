# IronFlow Deterministic Performance Matrix

- Generated: `2026-04-17T03:59:43.942516+00:00`
- Git SHA: `5173d32adb89f44dcdec668feea652020fadc1bb`
- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.13.12`
- Raw JSON: `docs/perf_candidate_post_createflow_fastpath.json`

## Recipe Results

| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | 2.020 | 64.29 | 1.992 | 1.973 | 4.776 | 0.382 | 1.819 | 1.247 | 0 |
| medium_wide_heavy_write_warm | 4.016 | 38.77 | 1.983 | 2.128 | 4.485 | 0.379 | 1.852 | 2.603 | 0 |
| small_narrow_few_write_cold | 0.247 | 131.14 | 7.771 | 1.812 | 4.523 | 0.329 | 0.898 | 0.147 | 0 |

## Anti-Flake Controls

- Deterministic random seed per recipe/iteration.
- Fixed recipe catalog with bounded sizes.
- Warmup iterations are excluded from aggregates.
- Metrics use medians/p95/p99 across multiple repetitions.
