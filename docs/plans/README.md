# Feature plans

Longer multi-phase designs live here. Keep root `AGENTS.md` / `MEMORY_BANK.md` as
the short handoff; link the active plan from the task brief.

| Plan | Status |
| --- | --- |
| [subflows.md](subflows.md) | Implemented (Phases 0–5) |
| [flow-run-final-state.md](flow-run-final-state.md) | Implemented — default wait_all aggregation + detach/explicit escape |
| [self-hosted-docker-auth.md](self-hosted-docker-auth.md) | Tier A+C + Tier B core shipped (#57); deferred HA/Redis/UI/GHCR |
| [self-hosted-storage-rfc.md](self-hosted-storage-rfc.md) | Accepted — persistence topology + Rust hot-path rules |
| [self-hosted-docker-tier-b.md](self-hosted-docker-tier-b.md) | 4-PR sequence complete (B0–B3/B5); follow-ups listed as deferred |
| [concurrency-limits.md](concurrency-limits.md) | Implemented (Phases 1–3 subset) — global + tag + rate_limit |

When starting a large compatibility feature, add a plan before coding and name it
in the PR body.
