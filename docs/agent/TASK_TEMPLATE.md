# Agent task brief (copy into issue / PR / agent prompt)

## Goal

One sentence: what should be true when this task is done?

## Ownership

- In scope paths/modules:
- Forbidden areas (do not edit):

## Acceptance criteria

- [ ] Behavior / API / docs expectation:
- [ ] Tests added or updated:
- [ ] Compatibility matrix updated? (`COMPATIBILITY.md`) yes/no/n/a
- [ ] MEMORY_BANK / plan note updated if user-visible? yes/no

## Validation commands

```bash
# from repo root — pick what applies
python3 -m pytest python-shim/tests static-planner/tests benchmarks/tests
cargo test --manifest-path rust-engine/Cargo.toml
python3 benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2
```

## Branch

`cursor/<short-description>-b2e5` (or repo cloud suffix) off `main`. One task per branch.

## Notes / hazards

- Hotspots in play:
- Prefers Rust hot path? yes/no
- Related plan: `docs/plans/…`
