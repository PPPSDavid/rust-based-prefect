# IronFlow Deterministic Performance Matrix

- Generated: `2026-04-17T02:46:28.225268+00:00`
- Git SHA: `5173d32adb89f44dcdec668feea652020fadc1bb`
- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.13.12`
- Raw JSON: `docs/perf_candidate_post_flow_batch_tx.json`

## Recipe Results

| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | 3.409 | 67.91 | 2.997 | 2.328 | 5.863 | 0.388 | 2.918 | 1.206 | 0 |
| medium_wide_heavy_write_warm | 4.666 | 39.38 | 2.212 | 2.274 | 4.823 | 0.418 | 2.072 | 3.094 | 0 |
| small_narrow_few_write_cold | 0.254 | 128.65 | 7.550 | 1.894 | 4.567 | 0.406 | 5.254 | 0.122 | 0 |

## Anti-Flake Controls

- Deterministic random seed per recipe/iteration.
- Fixed recipe catalog with bounded sizes.
- Warmup iterations are excluded from aggregates.
- Metrics use medians/p95/p99 across multiple repetitions.
