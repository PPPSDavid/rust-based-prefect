# IronFlow Deterministic Performance Matrix

- Generated: `2026-04-17T02:35:05.927036+00:00`
- Git SHA: `5173d32adb89f44dcdec668feea652020fadc1bb`
- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.13.12`
- Raw JSON: `docs/perf_baseline_pre_next_step.json`

## Recipe Results

| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | 1.692 | 75.04 | 1.717 | 1.855 | 4.472 | 0.301 | 1.328 | 1.016 | 0 |
| medium_wide_heavy_write_warm | 3.413 | 44.68 | 1.712 | 1.841 | 4.384 | 0.299 | 1.293 | 1.994 | 0 |
| small_narrow_few_write_cold | 0.247 | 134.40 | 8.374 | 1.934 | 4.250 | 0.355 | 5.662 | 0.150 | 0 |

## Anti-Flake Controls

- Deterministic random seed per recipe/iteration.
- Fixed recipe catalog with bounded sizes.
- Warmup iterations are excluded from aggregates.
- Metrics use medians/p95/p99 across multiple repetitions.
