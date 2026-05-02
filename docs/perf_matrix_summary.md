# IronFlow Deterministic Performance Matrix

- Generated: `2026-05-02T01:51:57.328920+00:00`
- Git SHA: `5793c8110d3b9685b441a68be205466bb7d2569f`
- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.13.12`
- Benchmark mode: `preset `lite`` (`preset:lite`)
- Raw JSON: `C:/Users/19665/SynologyDrive/Coding Projects/rust-based-prefect/docs/perf_matrix_results.json`

## Recipe Results

| Recipe | Wall p95 (s) | Throughput transitions/s p95 | p95 create flow (ms) | p95 create task (ms) | p95 set flow state (ms) | p95 record task event (ms) | p95 read query (ms) | CPU sec p95 | RSS delta p95 (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| medium_narrow_heavy_mixed_warm | 1.960 | 61.23 | 2.159 | 2.742 | 5.646 | 0.520 | 6.028 | 1.094 | 0 |
| small_narrow_few_write_cold | 0.279 | 107.34 | 9.238 | 2.289 | 5.901 | 0.470 | 6.268 | 0.078 | 0 |

## Anti-Flake Controls

- Deterministic random seed per recipe/iteration.
- Fixed recipe catalog with bounded sizes.
- Warmup iterations are excluded from aggregates.
- Metrics use medians/p95/p99 across multiple repetitions.
