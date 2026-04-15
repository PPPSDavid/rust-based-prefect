# Benchmark Baseline and Success Metrics

This baseline defines what we consider "better than current pain points" for the MVP.

## Workload Scenarios

1. **Flow fan-out chain**
   - 100, 1,000, and 10,000 task runs.
   - Pattern: `task1.map -> task2.map -> task3.map`.
2. **High run creation pressure**
   - Burst create 1,000 flow runs with modest task count.
3. **State transition churn**
   - Frequent retries and heartbeat-like transition updates.
4. **Concurrency-limited execution**
   - Global limit and tag-based limits under queue pressure.

## Measured Metrics

- Transition throughput (transitions/sec).
- Transition p95 latency.
- Scheduler queue depth and time-in-queue.
- Duplicate transition rejection correctness.
- Final state determinism (same event stream => same result).
- Flow completion rate under load.

## Gate Targets (MVP)

- No double-scheduling under synthetic race tests.
- Stable processing at 10,000 transitions without deadlock.
- p95 transition latency under 250 ms in local stress runs.
- >99% successful completion for non-failing benchmark runs.
- Deterministic replay validation passes for all benchmark scenarios.

## Baseline Inputs To Record

- CPU, memory, local DB backend choice.
- Python and Rust versions.
- Prefect baseline version and compatibility mode.
- Benchmark script commit hash.
