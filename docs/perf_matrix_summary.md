# IronFlow Deterministic Performance Matrix

- Generated: `2026-05-01T03:35:46.347186+00:00`
- Git SHA: `54e99e6771f043fe2a6b5e5bbcc0103ce0518c74`
- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.13.12`
- Benchmark mode: `preset `lite`` (`preset:lite`)
- Raw JSON: `C:/Users/19665/SynologyDrive/Coding Projects/rust-based-prefect/docs/perf_matrix_results.json`

## Recipe Results

| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | 2.121 | 56.57 | 2.415 | 2.712 | 6.694 | 0.438 | 1.778 | 1.156 | 0 |
| small_narrow_few_write_cold | 0.292 | 102.64 | 8.283 | 4.533 | 6.074 | 0.321 | 5.864 | 0.062 | 0 |

## Anti-Flake Controls

- Deterministic random seed per recipe/iteration.
- Fixed recipe catalog with bounded sizes.
- Warmup iterations are excluded from aggregates.
- Metrics use medians/p95/p99 across multiple repetitions.
