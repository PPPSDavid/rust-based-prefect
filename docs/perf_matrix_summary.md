# IronFlow Deterministic Performance Matrix

- Generated: `2026-04-17T21:49:21.953072+00:00`
- Git SHA: `5173d32adb89f44dcdec668feea652020fadc1bb`
- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.13.12`
- Raw JSON: `C:/Users/19665/SynologyDrive/Coding Projects/rust-based-prefect/docs/perf_matrix_results.json`

## Recipe Results

| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | 2.009 | 59.73 | 3.337 | 4.469 | 8.305 | 0.377 | 3.016 | 1.109 | 0 |
| small_narrow_few_write_cold | 0.358 | 83.78 | 8.028 | 4.490 | 9.010 | 0.413 | 5.974 | 0.031 | 0 |

## Anti-Flake Controls

- Deterministic random seed per recipe/iteration.
- Fixed recipe catalog with bounded sizes.
- Warmup iterations are excluded from aggregates.
- Metrics use medians/p95/p99 across multiple repetitions.
