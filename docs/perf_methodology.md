# IronFlow Performance Methodology

For **Prefect vs IronFlow headline throughput** (synthetic A/B, caveats, and multipliers), see [Performance overview](PERFORMANCE_OVERVIEW.md).

This document describes the deterministic benchmark harness in `benchmarks/perf_matrix.py`.
The harness is designed for stable cross-commit comparison, regression detection, and CI use.

## Single Entrypoint

Always use a **subcommand**: `run` or `compare` (there is no bare `perf_matrix.py` invocation).

- Run benchmark suite:
  - `python benchmarks/perf_matrix.py run --preset full`
- Compare baseline vs candidate (both files must be **`run` outputs** — JSON **objects** with `aggregates`):
  - `python benchmarks/perf_matrix.py compare --baseline docs/perf_baseline_example.json --candidate docs/perf_candidate_example.json`

Do **not** use `docs/perf_comparison.json` (from `compare_prefect_vs_ironflow.py`) as an input to `compare`; that file is a JSON **array** and will be rejected with an explicit error.

Fast local loop:

- `python benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2`

Transition-hook microbench (same `perf_matrix` harness; exercises real `@flow` / `@task` shim path with optional no-op hooks on flow, task, or both):

- `python benchmarks/perf_matrix.py run --preset hook_micro --repetitions 3 --warmups 1 --jobs 1`

Concurrency / FSM batch regression gate (FSM batch transitions + multi-reader mixed workload):

- `python benchmarks/perf_matrix.py run --preset concurrency --repetitions 2 --warmups 1 --jobs 1`

## Workload Dimensions

The recipe catalog spans:

- `flow_count` (small/medium/large)
- `tasks_per_flow` (narrow/wide)
- transition density (`task_events_per_task`: few/heavy)
- read-heavy vs write-heavy (`read_ratio`)
- mixed workload (`mixed=True`: concurrent read + write; optional `mixed_reader_count` for N reader threads)
- FSM batch path (`fsm_task_lifecycle=True`: `record_task_events_batch` with pending/running/completed instead of heartbeat events)
- cold start vs warm run (`cold_start`)
- optional **decorator transition-hook microbench** (`decorator_hook_profile` on select recipes): `flow_count` is timed shim iterations; `tasks_per_flow` is warmup iterations before the timer; profiles `none` / `flow` / `task` / `both` compare baseline vs no-op hooks.

Use defaults for consistency (`--preset lite`, `--preset pr`, `--preset hook_micro`, `--preset concurrency`, or `--preset full`) or override with:

- `--recipes small_narrow_few_write_cold,medium_wide_heavy_write_warm`
- `--repetitions 5 --warmups 2 --seed 20260416`
- `--jobs 2` (parallelize recipes across worker processes)

## Captured Metrics

For each recipe/iteration, the runner records:

- wall-clock duration
- throughput
  - `transitions_per_sec`
  - `tasks_per_sec`
  - `flows_per_sec`
- latency percentiles (p50/p95/p99) for critical operations
  - `create_flow`
  - `create_task`
  - `set_flow_state`
  - `record_task_event`
  - key read queries:
    - `query.list_flow_runs`
    - `query.list_task_runs`
    - `query.list_events`
    - `query.get_flow_run_detail`
  - for `micro_decorator_hooks_*` recipes: `decorator_hook_micro.invocation_ms` (per `@flow` invocation, including `@task.submit` work)
- process-level CPU and RSS memory
  - `process.cpu_seconds_used`
  - `process.rss_bytes_start/end/delta`
- SQLite growth and write amplification indicators
  - db/wal growth
  - `sqlite.bytes_per_write_op`

## Output Format

`run` command emits:

- machine-readable JSON (default `docs/perf_matrix_results.json`)
- markdown report (default `docs/perf_matrix_summary.md`)

JSON metadata includes:

- git commit SHA
- timestamp (UTC)
- OS
- Python version
- run seed, repetitions, warmups, jobs
- **`matrix_compare_key`** — canonical **benchmark mode** (e.g. `preset:lite`, `preset:full`, or `recipes:...` for a custom recipe set). Comparisons are valid **only** when baseline and candidate share the same key.
- **`matrix_mode`** — short human-readable label for the same mode

Legacy `run` JSON without these fields can still be compared: the key is inferred from `recipes` or from aggregate recipe names.

## Regression Detection

Use `compare` only with two artifacts produced by **`perf_matrix.py run`** (same workload family). If the baseline was captured with `--preset lite` and the candidate with `--preset full`, the tool **does not** emit fake regressions: it **skips** comparison and reports a mode mismatch (see exit `3` below).

```bash
python benchmarks/perf_matrix.py compare \
  --baseline docs/perf_baseline_example.json \
  --candidate docs/perf_candidate_example.json \
  --thresholds "latency_ms.create_flow.p95=0.10,latency_ms.set_flow_state.p95=0.10,throughput.transitions_per_sec.median=0.10,wall_clock_seconds.p95=0.10"
```

- Threshold values are ratio limits (0.10 = 10%).
- Latency/wall metrics regress when candidate is higher than threshold.
- Throughput metrics regress when candidate is lower than threshold.
- Exit codes:
  - `0` — compared; no regression
  - `2` — compared; regression detected (CI-friendly fail)
  - `3` — **not compared** — baseline and candidate **benchmark modes** differ (`matrix_compare_key`). Capture a new baseline using the same `--preset` or `--recipes` as the candidate.
  - `1` — input error (e.g. file is not a `perf_matrix` run object)

Outputs:

- JSON diff report (default `docs/perf_compare_report.json`)
- markdown diff report (default `docs/perf_compare_report.md`)

## CI Integration

Workflow: `.github/workflows/perf-benchmarks.yml`

- PRs (Linux `perf-pr`): reduced stable suite (`--preset lite --repetitions 2 --warmups 1 --jobs 2`); uploads JSON + markdown. This is the primary PR gate path on **ubuntu-latest** only.
- PRs (`perf-pr-cross`): the same **lite** recipe set runs on **ubuntu-latest**, **windows-latest**, and **macos-latest** with lighter sampling (`--repetitions 1 --warmups 0 --jobs 2`) and **per-OS artifact uploads only** (informational signal; no `perf_matrix compare` across OS against a single baseline, which would be invalid for `matrix_compare_key` / methodology reasons).
- main/schedule: fuller suite (`--preset full --repetitions 5 --warmups 2`)
- artifacts uploaded:
  - benchmark JSON
  - markdown report

## Interpretation

When reviewing changes:

- prioritize p95/p99 for user-facing operations
- treat throughput and latency together (a throughput gain with large p95 latency regression is suspicious)
- check RSS and SQLite growth for long-run sustainability
- compare only like-for-like modes (`matrix_compare_key`), not different presets mixed in one `compare` call

## Anti-Flake Measures (Design Note)

- deterministic seeds per recipe and iteration
- fixed bounded recipe catalog for CI stability
- separate warmups from measured iterations
- medians and tail percentiles over repeated runs
- no unbounded background services in the core harness path

## Concurrency and Locking (Read/Write Contract)

IronFlow optimizes **read throughput** without sacrificing **single-writer determinism** on the FSM.
Agents and contributors must preserve this contract when touching `runtime.py` or the Rust FFI.

### Write path (must stay serialized per control plane)

- `InMemoryControlPlane` holds an `RLock` (`_lock`) around all mutations: flow/task creation, state transitions, event append, and Python SQLite writes.
- Rust FSM calls (`ironflow_control`) take a **per-handle** mutex. Different engine handles can dispatch in parallel; a single handle is still single-writer.
- **Do not** remove the Python write lock or shard it by flow without an explicit determinism design — optimistic concurrency and transition tokens assume ordered application per plane.

### Read path (intentionally concurrent)

- List/detail query methods (`list_flow_runs`, `list_task_runs`, `list_events`, etc.) call `_query_rust()` **without** acquiring `_lock`.
- `ironflow_query` uses a **thread-local SQLite connection pool** (one reused connection per thread per database path) under WAL mode.
- Mixed `perf_matrix` recipes spawn one writer thread plus `mixed_reader_count` reader threads to regression-test this path.

### Task runners vs control plane

- `ThreadPoolTaskRunner` / `ProcessPoolTaskRunner` parallelize **user task bodies** in `map()` / `submit()`.
- Every `submit()` still records transitions through the serialized control plane. Do not expect task-runner threads to speed up transition-heavy benchmarks.

### Batch APIs

- `set_flow_states_batch` and `record_task_events_batch` collapse multiple FSM transitions into one SQLite transaction (threshold: 2+ items).
- `@flow` / `@task` decorators and the `concurrency` perf preset exercise these paths; heartbeat-only workloads do not.

### Benchmark interpretation

- `--jobs N` parallelizes **recipes across processes**, not operations inside a recipe.
- Compare runs only when `metadata.matrix_compare_key` matches (e.g. `preset:concurrency` vs `preset:concurrency`).

See example regression artifacts:

- `docs/perf_baseline_example.json`
- `docs/perf_candidate_example.json`
- `docs/perf_compare_example.md`
- `docs/perf_design_note.md`