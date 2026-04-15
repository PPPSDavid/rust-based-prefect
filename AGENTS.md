# Agent Working Guide

This repository is designed for iterative agent-assisted development. Use this guide as first-read context.

## Mission

Deliver a Prefect-compatible orchestration prototype that improves:

- state transition correctness and determinism
- scale/performance under high run and task volume
- pre-run graph visibility and forecasting for analyzable flow definitions

## Ground Truth Files

- `README.md` for project entry points
- `COMPATIBILITY.md` for supported Prefect semantics
- `docs/MEMORY_BANK.md` for session handoff context
- `docs/perf_methodology.md` and `docs/perf_comparison.json` for benchmark baseline

## Development Conventions

- Keep compatibility decisions explicit in `COMPATIBILITY.md`.
- Prefer additive, test-backed changes over broad rewrites.
- If behavior changes, update tests and docs in same change.
- Preserve benchmark reproducibility (do not silently change workload shapes).

## Repo Map

- `rust-engine/`: deterministic orchestration kernel
- `python-shim/`: Prefect-style API/compatibility runtime
- `static-planner/`: static flow analysis and forecasting
- `benchmarks/`: cross-engine performance measurements
- `scripts/`: utility scripts for stress/forecast outputs

## Expected Validation

Run before declaring completion:

1. `python -m pytest python-shim/tests static-planner/tests`
2. `cargo test --manifest-path rust-engine/Cargo.toml`
3. If performance logic changed: `python benchmarks/compare_prefect_vs_ironflow.py`

## MCP and Tooling Guidance

- Prefer `gh` CLI for GitHub workflows (issues/PRs/releases).
- Use browser automation MCP only when UI validation is required.
- Keep benchmark output artifacts in `docs/` and avoid checking in noisy transient logs.

## Decision Defaults for Future Agents

- Prioritize correctness and deterministic behavior over feature breadth.
- Prefer subset parity with strong tests over incomplete full parity claims.
- If adding new compatibility surface, add at least one script-level E2E test.

## Review Process

For any code changes that are significant (except bugfixs etc), please adopt a review / iterationi process of the following: 

```  
Simulate three different experts answering the question below.
Each expert will write down one step of their thinking and share it with the group.
Then all experts move to the next step.
If any expert realizes they're wrong at any point, they drop out.
Continue until there’s consensus on the final answer.  
``` 

