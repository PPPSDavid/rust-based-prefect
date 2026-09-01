# Contributing to IronFlow

This is the command reference for working from a **repository checkout**. Agents and validation details live in [`AGENTS.md`](AGENTS.md). Releases: [`RELEASING.md`](RELEASING.md). Compatibility claims: [`COMPATIBILITY.md`](COMPATIBILITY.md). End-user install (PyPI wheels, no clone) is in the hosted [Installation](https://pppsdavid.github.io/rust-based-prefect/INSTALL/) guide.

## Setup

**uv (recommended; matches CI / Cursor Cloud):**

```bash
# Install uv: https://docs.astral.sh/uv/getting-started/installation/
uv sync --frozen --group dev
cargo build --manifest-path rust-engine/Cargo.toml
```

This uses the root workspace (`python-shim` + `static-planner`) and the committed `uv.lock`. Prefer `uv run pytest …` / `uv run ruff …` afterward.

**Conda (optional; full desktop stack including Prefect pin):**

```bash
mamba env create -f environment.yml   # or: conda env create -f environment.yml
conda activate ironflow-dev
cargo build --manifest-path rust-engine/Cargo.toml
```

**pip only (transitional, no uv):**

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements-ci.txt
cargo build --manifest-path rust-engine/Cargo.toml
```

Python **3.11+** is supported; `.python-version` / `environment.yml` default to **3.12**. Published wheels on PyPI target **CPython 3.11 and 3.12**; other versions may install from **sdist** or require this source checkout + `cargo build`.

The Python shim auto-discovers `ironflow_engine` under `rust-engine/target/`. Override with **`IRONFLOW_RUST_LIB`** if you build elsewhere. Skipping the cargo step leaves Python fallbacks where implemented; treat **`cargo build` as part of the normal full stack**.

Canonical bootstrap:

```bash
python scripts/bootstrap.py --check-only
python scripts/bootstrap.py
```

Numbered-release checkout vs git-install of the Python packages alone: [Installation](https://pppsdavid.github.io/rust-based-prefect/INSTALL/) §2–6.

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

From the repo root (`pytest.ini` sets `PYTHONPATH` for all packages):

```bash
python scripts/check_version_sync.py   # optional: verify VERSION ↔ artifacts
uv run pytest python-shim/tests static-planner/tests benchmarks/tests
uv run pytest -m airtight
cargo test --manifest-path rust-engine/Cargo.toml
npm --prefix frontend test
npm --prefix frontend run build
```

Control-plane or FFI changes also need a lite `perf_matrix` run (see **Expected Validation** in `AGENTS.md`). Write outputs under `/tmp`, not tracked `docs/perf_matrix_*`, unless you intend to refresh the published baseline.

## Optional API + UI

```bash
python scripts/ironflow_server.py start
python scripts/ironflow_server.py doctor
```

- API: `http://127.0.0.1:8000` — e.g. `GET /health`, `GET /api/flow-runs`
- UI: `http://localhost:4173` (typical Vite port; use `localhost`, not `127.0.0.1`)

Backend only: `python scripts/ironflow_server.py start --backend-only`. Seed demo runs with `python scripts/ui_e2e_seed.py`. Details: [How to run the server and UI](https://pppsdavid.github.io/rust-based-prefect/how-to/server-and-ui/).

## Benchmarks

IronFlow targets **control-plane** performance (state transitions, scheduling), not faster arbitrary Python in tasks. Caveats and tables: [Performance overview](https://pppsdavid.github.io/rust-based-prefect/PERFORMANCE_OVERVIEW/).

- **Prefect vs IronFlow A/B** (optional; needs Prefect installed):
  `python benchmarks/compare_prefect_vs_ironflow.py` → writes `docs/perf_comparison.json` (JSON **array** — not for `perf_matrix.py compare`).
- **Deterministic control-plane matrix** (IronFlow vs IronFlow regressions):
  `python benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2`
  See [perf_methodology.md](docs/perf_methodology.md) and **Performance** in [AGENTS.md](AGENTS.md).

## Persistence defaults

- JSONL history: `data/ironflow_history.jsonl` or `IRONFLOW_HISTORY_PATH`
- SQLite read model: sidecar `.db` next to the JSONL path, or `data/ironflow_ui.db` when defaults apply — see [Environment variables](https://pppsdavid.github.io/rust-based-prefect/reference/env-vars/).

## Building docs locally

The **GitHub Pages** site ([https://pppsdavid.github.io/rust-based-prefect/](https://pppsdavid.github.io/rust-based-prefect/)) is **end-user** documentation. Maintainer topics such as [DISTRIBUTION.md](docs/DISTRIBUTION.md) and [perf_methodology.md](docs/perf_methodology.md) stay in the repository but are **not** published to the site.

```bash
python -m pip install -r requirements-docs.txt
mkdocs serve
```

The hosted site tracks **`main`**. For documentation that exactly matches a tag, browse GitHub at that tag, or checkout the tag and run `mkdocs serve`.

## Pull requests

- One topic per branch (`feat/`, `fix/`, `refactor/`, `test/` — see `AGENTS.md`).
- Do not claim Prefect parity without updating `COMPATIBILITY.md` and tests in the same change.
- Prefer adding modules over growing hotspot files (`runtime.py` facade, `server.py`, `ffi.rs`, `prefect_compat/__init__.py`).
