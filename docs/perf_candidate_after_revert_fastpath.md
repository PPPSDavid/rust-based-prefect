# IronFlow Deterministic Performance Matrix

- Generated: `2026-04-17T04:02:54.196778+00:00`
- Git SHA: `5173d32adb89f44dcdec668feea652020fadc1bb`
- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.13.12`
- Raw JSON: `docs/perf_candidate_after_revert_fastpath.json`

## Recipe Results

| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | 1.616 | 75.43 | 1.705 | 1.772 | 4.299 | 0.285 | 1.320 | 0.978 | 0 |
| medium_wide_heavy_write_warm | 3.480 | 45.20 | 1.666 | 1.789 | 3.972 | 0.289 | 1.318 | 1.988 | 0 |
| small_narrow_few_write_cold | 0.234 | 135.37 | 7.584 | 1.790 | 4.307 | 0.298 | 0.845 | 0.094 | 0 |

## Anti-Flake Controls

- Deterministic random seed per recipe/iteration.
- Fixed recipe catalog with bounded sizes.
- Warmup iterations are excluded from aggregates.
- Metrics use medians/p95/p99 across multiple repetitions.
