# IronFlow Deterministic Performance Matrix

- Generated: `2026-04-18T21:44:25.202414+00:00`
- Git SHA: `905bb0e0da2e24a7c7ed7ea08358c01261cd03b8`
- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.13.12`
- Benchmark mode: `preset `lite`` (`preset:lite`)
- Raw JSON: `C:/Users/19665/SynologyDrive/Coding Projects/rust-based-prefect/docs/perf_matrix_results.json`

## Recipe Results

| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | 1.530 | 78.41 | 2.045 | 2.025 | 5.901 | 0.254 | 4.203 | 0.922 | 0 |
| small_narrow_few_write_cold | 0.276 | 108.73 | 6.485 | 2.218 | 7.339 | 0.252 | 4.703 | 0.062 | 0 |

## Anti-Flake Controls

- Deterministic random seed per recipe/iteration.
- Fixed recipe catalog with bounded sizes.
- Warmup iterations are excluded from aggregates.
- Metrics use medians/p95/p99 across multiple repetitions.
