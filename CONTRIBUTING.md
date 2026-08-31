# Contributing to IronFlow

This is a short human-oriented guide. Agents and detailed validation live in [`AGENTS.md`](AGENTS.md). Releases: [`RELEASING.md`](RELEASING.md). Compatibility claims: [`COMPATIBILITY.md`](COMPATIBILITY.md).

## Setup

```bash
uv sync --frozen --group dev
cargo build --manifest-path rust-engine/Cargo.toml
```

See the root `README.md` for conda/pip fallbacks and the hosted [Installation](https://pppsdavid.github.io/rust-based-prefect/INSTALL/) guide.

## Lint, format, complexity

Run the wrapper (ruff, ty, rustfmt, clippy, file-LOC ratchet):

```bash
bash scripts/lint.sh
```

Or individually:

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
python scripts/code_metrics.py
cargo fmt --manifest-path rust-engine/Cargo.toml -- --check
cargo clippy --manifest-path rust-engine/Cargo.toml --all-targets -- -D warnings
```

**File size caps** (enforced by `scripts/code_metrics.py`, not ruff):

- New production files in `python-shim/src`, `rust-engine/src`, `static-planner/src`, `frontend/src`: **≤800 lines**.
- Existing files must not cross **1000 lines** unless they are on the allowlist in `scripts/metrics/baseline.json`, and allowlisted files **must not grow**.
- Function complexity: ruff `C901` (McCabe, max 20) and clippy `too_many_lines` (threshold 120).

Do not “fix” LOC by deleting comments, packing lines, or moving production logic into tests.

Optional git hooks: `pre-commit install` (ruff + rustfmt only; clippy is too slow for every commit).

## Tests

```bash
uv run pytest python-shim/tests static-planner/tests benchmarks/tests
uv run pytest -m airtight
cargo test --manifest-path rust-engine/Cargo.toml
npm --prefix frontend test
npm --prefix frontend run build
```

Control-plane or FFI changes also need a lite `perf_matrix` run (see **Expected Validation** in `AGENTS.md`). Write outputs under `/tmp`, not tracked `docs/perf_matrix_*`, unless you intend to refresh the published baseline.

## Pull requests

- One topic per branch (`feat/`, `fix/`, `refactor/`, `test/` — see `AGENTS.md`).
- Do not claim Prefect parity without updating `COMPATIBILITY.md` and tests in the same change.
- Prefer adding modules over growing hotspot files (`runtime.py` facade, `server.py`, `ffi.rs`, `prefect_compat/__init__.py`).
