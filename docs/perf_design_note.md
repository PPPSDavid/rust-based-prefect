# Performance Harness Design Note

The benchmark harness in `benchmarks/perf_matrix.py` targets repeatability over peak synthetic throughput.

## Methodology

- Execute fixed workload recipes that explicitly encode:
  - flow count
  - tasks per flow
  - transition/event density
  - read/write ratio
  - mixed concurrent read+write behavior
  - cold vs warm startup mode
- Collect operation-level latency distributions (p50/p95/p99) for write and read primitives.
- Collect throughput, wall-clock, CPU, RSS, and SQLite growth/write amplification.
- Aggregate with medians and tail percentiles across repetitions.

## Anti-Flake Controls

- deterministic seeding per recipe/iteration
- bounded workload sizes in CI presets
- warmup iterations separated from measured iterations
- reduced PR suite and fuller main/nightly suite
- comparison thresholds evaluated on normalized percentages

## Why This Approach

This shape is stable enough for PR gating while still exposing regressions in:

- tail latency (`p95`/`p99`)
- throughput drops
- cold-start costs
- memory and storage growth characteristics
