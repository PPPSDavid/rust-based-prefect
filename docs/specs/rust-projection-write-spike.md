# Rust projection write spike (May 2026)

## Goal

Feasibility slice for backlog item *move projection write hot paths from Python into Rust* (`docs/MEMORY_BANK.md`): one SQLite read-model write moved behind optional native code while preserving default Python behavior.

## Target chosen

**`task_runs` row update** — `InMemoryControlPlane._update_task_row` in `python-shim/src/prefect_compat/runtime.py`.

Why this slice:

- **Expert A (perf):** Task events scale with nodes and retries; `_update_task_row` runs on replay and live transitions — frequent and simple SQL (`UPDATE task_runs SET …`).
- **Expert B (compat):** Signature is stable (state string + version + timestamp); matches existing Rust `ui_write` SQL for task transitions.
- **Expert C (risk):** Single-row `UPDATE` has clear idempotent replay semantics (same values reapplied — same row state).

Consensus: lowest coupling for a first FFI surface; broader projections (events + logs + artifacts) stay on Python until connection reuse / batching exists.

## Behavior

| Item | Detail |
|------|--------|
| Opt-in | Environment variable `IRONFLOW_RUST_PROJECTION` set to `1`, `true`, or `yes`. |
| Default | Unset or off → Python `sqlite3` path only (no behavior change). |
| Missing symbol | Older `ironflow_engine` builds without `ironflow_projection_update_task_run` → silent fallback (debug log only). |

## FFI

| Export | Meaning |
|--------|---------|
| `ironflow_projection_update_task_run(db_path, task_run_id, state, version, updated_at) -> i32` | Returns `0` success, `1` invalid FFI input, `2` SQLite error. |

Rust module: `rust-engine/src/projection.rs`. Python wiring: `try_rust_projection_update_task_run` in `python-shim/src/prefect_compat/rust_bridge.py` (ctypes; symbol guarded with `getattr`).

## Idempotency / replay

Same `UPDATE` text as Python; replaying the same logical transition yields the same row contents. Event/log projection remains unchanged in this spike.

## Risks

1. **Separate connections:** Rust opens `rusqlite::Connection::open(path)` per call; Python holds `_sqlite_conn`. SQLite serializes writers — correct but extra lock churn vs a shared handle.
2. **Transaction boundaries:** Python-side transactions are invisible to Rust; mixed use inside one transaction is unsafe — future work should bind the native layer to the existing connection or standardize “projection worker” ownership.
3. **Stale release DLL:** Loader prefers `rust-engine/target/release/` before `debug/` on Windows; adding exports requires rebuilding release for packaged wheels / local testing consistency.

## Micro-benchmark (authoritative command)

```bash
cargo build --manifest-path rust-engine/Cargo.toml --release
python benchmarks/rust_projection_spike.py
```

### Sample run (Windows 11, developer machine, May 2026)

```
Python sqlite UPDATE x2000: median=3.4875 ms  p95=4.0272 ms
Rust FFI UPDATE x2000: median=4.2512 ms  p95=4.9404 ms
Median speedup (Python / Rust): 0.82x
```

Interpretation: Python keeps one open `sqlite3` connection for all iterations; Rust benchmarks **open + execute + close** per iteration via FFI. This measures **current spike shape**, not an optimized Rust hot path. Expected gains require **connection affinity**, optional **batching**, or moving larger chunks (events + logs) atomically.

## Follow-ups

1. Expose a **bound SQLite connection** or connection pool from Python (or long-lived Rust handle) for projection writes.
2. Extend spike to **`INSERT OR IGNORE` events** or batched multi-row writes behind the same flag family.
3. CI: ensure **release** artifacts include new exports before publishing wheels.

## Tests

- `python-shim/tests/test_rust_projection_spike.py` — parity of resulting row vs Python `UPDATE` when opt-in and symbol present; disabled path leaves row unchanged.
- `rust-engine::projection::tests` — unit test for SQL effects.
