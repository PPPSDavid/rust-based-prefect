# Adoption Wedge Design: Install Reliability + One-Command Local Stack

Date: 2026-04-30  
Status: Draft approved in chat, pending implementation planning  
Owner: Project IronFlow maintainers

## 1) Goal and Product Promise

For the next 6-8 weeks, IronFlow should make one clear promise:

> A new user can install IronFlow, run with the Rust engine enabled, and start the local API/UI stack with one command and actionable diagnostics.

This scope intentionally prioritizes:

- easier first-run success for Prefect-savvy evaluators and contributors,
- architecture fidelity (Rust engine remains central),
- practical trust signals in docs and command output.

This scope intentionally does not claim full Prefect parity.

## 2) Target Audience and Positioning

Primary audiences for this phase:

1. Existing Prefect users evaluating migration or local alternatives.
2. Performance-focused evaluators testing control-plane throughput claims.
3. Potential contributors deciding whether the project is mature enough to join.

Non-goal for this phase:

- optimize onboarding for users who are entirely new to orchestration concepts.

## 3) Constraints and Decision Drivers

Project constraints selected during brainstorming:

- Keep a balanced roadmap between UX improvements and technical rigor.
- Preserve Rust-first control-plane architecture.
- Pace design for approximately 6-10 hours/week of maintainer time.
- Keep compatibility claims explicit and conservative via `COMPATIBILITY.md`.

Decision implications:

- We can improve install and startup ergonomics, but should not hide or bypass Rust engine requirements in the canonical path.
- We should support practical fallbacks only when clearly labeled as fallback behavior.

## 4) Approach Options Considered

### Option A (Recommended): Adoption Wedge

Parallel, shippable workstreams:

- install reliability first,
- one-command stack hardening in parallel,
- trust-signal docs improvements,
- contributor traction follow-ups.

Why recommended:

- best balance of visible progress and technical correctness,
- creates immediate user value without over-scoping infrastructure.

### Option B: Infra-First Hardening

Focus heavily on packaging/release internals first, UX later.

Trade-off:

- strongest long-term foundation, but weaker short-term traction.

### Option C: Demo-First Polish

Prioritize UI/demo polish before install automation depth.

Trade-off:

- quick visual impact, but higher risk of early user drop-off when setup fails.

## 5) Workstreams

## 5.1 Install Reliability (Priority 1)

Create a canonical bootstrap path that is deterministic and Rust-first.

Candidate deliverables:

- `scripts/bootstrap.py`:
  - verifies Python and Rust toolchain prerequisites,
  - builds `rust-engine`,
  - validates dynamic library loading path (including `IRONFLOW_RUST_LIB` overrides),
  - runs a smoke flow check and prints pass/fail guidance.
- docs simplification:
  - one primary install path in `README.md` and `docs/INSTALL.md`,
  - clearly marked alternates for advanced cases.
- post-install self-check command (or bootstrap subcommand) users can rerun anytime.

## 5.2 One-Command Local Stack (Priority 1)

Harden `python scripts/ironflow_server.py start` into a reliable operator entrypoint.

Candidate deliverables:

- preflight checks:
  - required ports,
  - backend dependencies,
  - frontend tooling availability,
  - Rust library readiness.
- clear readiness output:
  - backend status,
  - frontend status,
  - where to open UI and API.
- `--doctor` diagnostics mode:
  - gather environment diagnostics,
  - emit actionable fixes for common failures, especially Windows-first issues.

## 5.3 Trust Signals in Public Docs (Priority 2)

Improve confidence without overstating maturity.

Candidate deliverables:

- concise compatibility snapshot in `README.md` linked to `COMPATIBILITY.md`,
- benchmark evidence block linked to reproducible `perf_matrix.py` commands and current artifacts,
- short "what is stable vs experimental" section near top-level docs.

## 5.4 Traction and Contributor UX (Priority 2)

Lower contribution friction after onboarding basics are improved.

Candidate deliverables:

- rewrite top of `README.md` into conversion-focused flow:
  - what IronFlow is,
  - why it is different,
  - 10-minute path,
  - known limits.
- create starter issues aligned to workstreams (install, startup diagnostics, docs).

## 6) Roadmap (6-8 Weeks)

### Weeks 1-2: Install MVP

- implement bootstrap script v1,
- update install docs and README quickstart,
- validate first-run success on Windows-focused setup.

Exit condition:

- fresh setup reaches successful Rust-backed run through documented steps.

### Weeks 3-4: One-Command Stack Hardening

- improve `ironflow_server.py start` preflight and readiness UX,
- add `--doctor`,
- tighten error messages for common local failures.

Exit condition:

- command starts reliably or fails with concrete next actions.

### Weeks 5-6: Trust Signal Layer

- publish compatibility snapshot and benchmark evidence section,
- align docs language around explicit subset parity.

Exit condition:

- repository front page communicates realistic capability and validation evidence.

### Weeks 7-8 (Optional Stretch): Contributor Traction

- create contributor-friendly issue slate and quickstart improvements,
- add migration-oriented mini-guide for Prefect users where scope permits.

Exit condition:

- at least three clearly scoped starter tasks exist and match current roadmap.

## 7) Acceptance Criteria

Phase is complete when all are true:

1. New user can follow docs and run a Rust-backed flow without manual troubleshooting beyond documented steps.
2. `ironflow_server.py start` provides explicit component health and actionable diagnostics on failure.
3. README provides a concise install/start/verify path with explicit limits.
4. At least one reproducible benchmark pointer and one compatibility snapshot are current and linked.

## 8) Risks and Mitigations

- Packaging complexity growth:
  - mitigate by maintaining one canonical path and treating alternatives as secondary.
- Windows environment variance:
  - mitigate with proactive bootstrap checks and `--doctor`, not only FAQ notes.
- Overstated parity expectations:
  - mitigate with explicit matrix language and conservative claims.
- Maintainer bandwidth pressure:
  - mitigate by shippable increments and strict scope control.

## 9) Out of Scope for This Design Cycle

- full Prefect API parity expansion,
- advanced cloud/tenant feature parity,
- broad UI redesign unrelated to install/start reliability,
- benchmark methodology overhauls.

## 10) Implementation Planning Hand-off

Next step after user review approval:

- invoke the writing-plans workflow and break this design into a concrete implementation plan with acceptance tests and sequencing by workstream.