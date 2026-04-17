# Performance: what speedup can you expect?

IronFlow is aimed at **control-plane throughput and determinism**, not at making every user task (HTTP calls, ML, ETL transforms) run faster. **Orchestration overhead** is a small slice of many real pipelines; in those cases, switching runtimes may change wall time only slightly. When you are **transition- or scheduler-bound** (many small tasks, heavy state churn, API hot paths), the gap can be large.

This page summarizes **reported A/B numbers** from the in-repo harness. Always treat them as **order-of-magnitude guidance** on a **narrow synthetic workload**, not a guarantee for your flows.

## What the A/B harness measures

Script: `benchmarks/compare_prefect_vs_ironflow.py` (run from the repository root)

It runs comparable **mapped** and **chained** toy flows at fixed “complexity,” counts control-plane **transition events**, and reports **transitions per second** (higher is better). Engines compared:

| Engine label | Meaning |
| --- | --- |
| **`ironflow`** | In-process `prefect_compat` + control plane (no HTTP). |
| **`ironflow_http`** | Same stack behind the local HTTP API boundary (adds request/serialization cost). |
| **`prefect`** | Local **Prefect OSS 3.x** running the analogous flow pattern. |

Output is written to `docs/perf_comparison.json` (JSON array — **not** an input to `perf_matrix.py compare`).

## Headline numbers (checked-in snapshot)

The table below uses the **committed** `docs/perf_comparison.json` snapshot. Re-run the script on your machine and Python/Prefect versions; ratios stay **qualitatively** similar in our experience, but absolute throughput varies.

**Transitions per second** (rounded):

| Pattern | Complexity | IronFlow (in-proc) | IronFlow + HTTP | Prefect OSS (local) |
| --- | ---: | ---: | ---: | ---: |
| **mapped** | 100 | ~67,000 | ~3,100 | ~29 |
| **mapped** | 500 | ~67,000 | ~3,150 | ~346 |
| **chained** | 100 | ~86,000 | ~3,300 | ~143 |
| **chained** | 500 | ~86,000 | ~3,500 | ~135 |

**Approximate multipliers** (IronFlow in-proc **÷** Prefect, same cell):

| Pattern | Complexity | ~× faster (transitions/s) |
| --- | ---: | ---: |
| mapped | 100 | **~2,300×** |
| mapped | 500 | **~190×** |
| chained | 100 | **~600×** |
| chained | 500 | **~640×** |

**HTTP boundary:** IronFlow through the local API is still **roughly one to two orders of magnitude** above Prefect on this harness (e.g. mapped @100 ~**110×**), because the comparison is dominated by orchestration cost, not network latency to a remote cluster.

## How to interpret this honestly

1. **Units:** “Faster” here means **higher control-plane transition throughput** on **synthetic** flows, not shorter time for arbitrary Python work inside tasks.
2. **Prefect baseline:** Numbers are for **local OSS** execution in this harness. Prefect Cloud, agents, and production tuning change the story.
3. **Your code dominates:** If 99% of wall time is inside task bodies (database, APIs, CPU work), **end-to-end** speedup may be **modest** even when IronFlow is much faster at scheduling.
4. **Subset product:** IronFlow does **not** implement full Prefect; faster orchestration only matters where you stay inside the supported subset ([Compatibility](compatibility.md)).

## Regression tracking (IronFlow vs IronFlow)

Contributors who compare IronFlow builds over time use the deterministic `perf_matrix.py` harness; methodology is documented in the repository at `docs/perf_methodology.md` (not required reading to **use** IronFlow).
