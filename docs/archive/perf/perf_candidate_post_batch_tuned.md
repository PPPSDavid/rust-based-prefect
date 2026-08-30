# IronFlow Deterministic Performance Matrix

- Generated: `2026-04-17T02:41:54.535290+00:00`
- Git SHA: `5173d32adb89f44dcdec668feea652020fadc1bb`
- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.13.12`
- Raw JSON: `docs/perf_candidate_post_batch_tuned.json`

## Recipe Results

| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | 1.736 | 74.58 | 1.744 | 1.780 | 4.167 | 0.297 | 1.317 | 0.981 | 0 |
| medium_wide_heavy_write_warm | 3.544 | 45.03 | 1.703 | 1.790 | 4.359 | 0.297 | 1.305 | 2.000 | 0 |
| small_narrow_few_write_cold | 0.257 | 127.27 | 8.519 | 2.029 | 5.731 | 0.374 | 0.945 | 0.125 | 0 |

## Anti-Flake Controls

- Deterministic random seed per recipe/iteration.
- Fixed recipe catalog with bounded sizes.
- Warmup iterations are excluded from aggregates.
- Metrics use medians/p95/p99 across multiple repetitions.
