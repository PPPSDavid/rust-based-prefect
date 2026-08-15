# Later plan — scale, hosted e2e, and remaining product surfaces

**Status:** P4.2/P4.3 shipped (`ironflow gcl` + UI Concurrency page). Remaining rows parked — re-score against the same north stars before starting.  
**North stars:** (1) airtight under overlap, (2) Prefect-easy to operate, (3) fast at volume without a multi-master FSM, (4) cheap proof it runs hosted.

## Why this is a separate plan

The now-column landed operator pause UI and concurrent-state proofs. The items below are still the right *direction*, but they are heavier or would mix unrelated ownership into one branch.

When this plan starts, pick **one** row per agent branch. Reject work that fights the write-lock contract or substitutes `perf_matrix` for airtightness.

## Queue (still scored against the north stars)

| ID | Item | Serves | Notes |
| --- | --- | --- | --- |
| P4.1 | Async `concurrency` / `rate_limit` | 2 | Thin over the same Rust acquire. |
| P4.4 | `perf_matrix --preset gcl` as **speed** gate | 3 | Keep distinct from `pytest -m airtight`. |
| Planning chrome | DAG forecast metrics, critical-path overlay, node inspector | 2 | Data already on the DAG payload. |
| P2.1 | Rust schedule/gate/GCL on Postgres | 3 | First real scale-shaped engine PR; Compose must not stay a Python fallback. |
| P2.4 | GHCR publish + GHA pull-and-smoke | 4 | Cheap hosted e2e. Not always-on cloud. |
| Optional VPS | One-shot tiny VPS + remote HTTP worker | 4 | Manual/`workflow_dispatch` only. |
| P5.3 / P6.1 / P7 | Artifacts, variables, automations design | 2 | Design-first for P7. |

## Explicitly still parked

Prefect Cloud tenancy, blocks/integrations, Dask/Ray, K8s/Docker/push pools, work-queue priority, human-input pause forms, pixel-perfect Prefect UI, always-on staging/EKS, sharding the FSM write lock.

## Related

- [`airtight-concurrency.md`](airtight-concurrency.md) — current correctness gate
- [`prefect-gap-canvas.md`](prefect-gap-canvas.md) — full backlog IDs
- [`self-hosted-docker-tier-b.md`](self-hosted-docker-tier-b.md) — GHCR / HA follow-ups
