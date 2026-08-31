# AGENTS — rust-engine

Ownership: deterministic orchestration kernel, FFI/cdylib, hot paths.

Read first: root `AGENTS.md`, `docs/architecture.md`, `COMPATIBILITY.md`.

## In scope

- State machine / transition validation
- Append-only history and Rust-backed query/schedule/claim paths
- `Cargo.toml` / crate layout (coordinate before lockfile churn)

## Out of scope (unless task says otherwise)

- Prefect decorator ergonomics (`python-shim/`)
- UI (`frontend/`)
- Benchmark recipe methodology changes without coordinating `benchmarks/`

## Validate

```bash
cargo fmt --manifest-path rust-engine/Cargo.toml -- --check
cargo clippy --manifest-path rust-engine/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path rust-engine/Cargo.toml
```

File-LOC ratchet (repo root): `python scripts/code_metrics.py`. New `rust-engine/src` files must be ≤800 lines.

If control-plane behavior changes, also run the lite perf gate from root `AGENTS.md`.
