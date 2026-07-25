# Feature plans

Longer multi-phase designs live here. Keep root `AGENTS.md` / `MEMORY_BANK.md` as
the short handoff; link the active plan from the task brief.

| Plan | Status |
| --- | --- |
| [prefect-gap-canvas.md](prefect-gap-canvas.md) | **Active backlog** — Prefect vs IronFlow gap canvas + sorted session queue (2026-07) |
| [flow-run-lifecycle-control.md](flow-run-lifecycle-control.md) | Design — cancel terminate + pause drain/terminate + resume (P3.2 expanded) |
| [ui-ops-experience.md](ui-ops-experience.md) | **Priority** — UI aesthetics + ops-scale UX; iterative render→confirm passes (**PU**) |
| [transition-hooks-priority.md](transition-hooks-priority.md) | **Priority** — X→Y→Z hooks on `@flow`/`@task` (core shipped; docs/ergonomics/worker gaps) (**PH**) |
| [subflows.md](subflows.md) | Implemented (Phases 0–5) |
| [flow-run-final-state.md](flow-run-final-state.md) | Implemented — default wait_all aggregation + detach/explicit escape |
| [self-hosted-docker-auth.md](self-hosted-docker-auth.md) | Tier A+C + Tier B core shipped (#57); deferred HA/Redis/UI/GHCR/migrator CLI |
| [self-hosted-storage-rfc.md](self-hosted-storage-rfc.md) | Accepted — persistence topology + Rust hot-path rules |
| [self-hosted-docker-tier-b.md](self-hosted-docker-tier-b.md) | 4-PR sequence complete (B0–B3/B5); follow-ups: HA, B4 Redis, Alembic CLI, UI/GHCR |
| [concurrency-limits.md](concurrency-limits.md) | Implemented (Phases 1–3 subset) — global + tag + rate_limit |

When starting a large compatibility feature, add a plan before coding and name it
in the PR body.
