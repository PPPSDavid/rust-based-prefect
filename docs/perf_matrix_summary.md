# IronFlow Deterministic Performance Matrix

- Generated: `2026-04-18T22:18:44.772843+00:00`
- Git SHA: `baf2ad480197a9862b0ffb3f5cb6bd40b33a03e1`
- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.13.12`
- Benchmark mode: `preset `lite`` (`preset:lite`)
- Raw JSON: `C:/Users/19665/SynologyDrive/Coding Projects/rust-based-prefect/docs/perf_matrix_results.json`

## Recipe Results

| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | 1.663 | 81.31 | 1.509 | 1.625 | 3.743 | 0.255 | 4.021 | 0.825 | 0 |
| small_narrow_few_write_cold | 0.285 | 127.08 | 7.070 | 2.272 | 5.866 | 0.319 | 4.679 | 0.091 | 0 |

## Anti-Flake Controls

- Deterministic random seed per recipe/iteration.
- Fixed recipe catalog with bounded sizes.
- Warmup iterations are excluded from aggregates.
- Metrics use medians/p95/p99 across multiple repetitions.
