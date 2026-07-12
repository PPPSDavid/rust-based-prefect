# How to set up IronFlow

If you only need the **`prefect_compat`** library, start with **[Installation](../INSTALL.md)** — **`pip install ironflow-prefect-compat`** or **`uv pip install ironflow-prefect-compat`** from PyPI when a wheel matches your platform.

After install, use the **Quick check after any install** on that page: the **`python -c`** one-liner, or (from a repo checkout) **`python scripts/bootstrap.py --native-check`** for a short in-process flow smoke. For clone-based development, **`python scripts/bootstrap.py --check-only`** validates toolchain hints before you build Rust.

This page consolidates the **repository / source** path: Python environment, **Rust** `rust-engine` build, and the environment variables the shim respects. For a minimal end-to-end check after setup, see **[Quick start (demo flow)](../QUICKSTART_DEMO.md)**.

## 1. Get the code

Pick a **[release tag](https://github.com/PPPSDavid/rust-based-prefect/releases)** for stability, or use `main` for the latest development state.

```bash
git clone https://github.com/PPPSDavid/rust-based-prefect.git
cd rust-based-prefect
git checkout v0.1.2   # optional: replace with current tag
```

## 2. Python environment

**uv (recommended; matches CI / Cursor Cloud)**

```bash
# Install uv: https://docs.astral.sh/uv/getting-started/installation/
uv sync --group dev
```

Then use `uv run pytest …`, `uv run ruff check .`, etc.

**Conda (optional; maintainers’ full desktop stack)**

```bash
mamba env create -f environment.yml    # or: conda env create -f environment.yml
conda activate ironflow-dev
```

**`venv` + pip (transitional, no uv)**

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-ci.txt
```

Use **Python 3.11+** (`.python-version` / `environment.yml` may pin 3.12).

## 3. Build the Rust engine

From the **repository root**:

```bash
cargo build --manifest-path rust-engine/Cargo.toml
```

For release builds:

```bash
cargo build --release --manifest-path rust-engine/Cargo.toml
```

## 4. Environment variables

| Variable | Purpose |
| --- | --- |
| **`IRONFLOW_RUST_LIB`** | Path to the built `ironflow_engine` shared library if it is **not** under `rust-engine/target/` (for example custom output directory). If unset, the shim searches default `cargo` output paths. Without a native library, some code paths use **Python fallbacks** where implemented—the intended stack is **always** build the `cdylib`. |
| **`IRONFLOW_HISTORY_PATH`** | When set to a file path, flow history can be **appended as JSONL** for inspection and tooling (see [Environment variables](../reference/env-vars.md) and the repository README). |
| **`PYTHONPATH`** | Set to `python-shim/src` at the repo root so `import prefect_compat` works **without** an editable install (used in **[Quick start](../QUICKSTART_DEMO.md)**). |

Task-runner–related optional variables (**`IRONFLOW_TASK_RUNNER`**, **`IRONFLOW_TASK_RUNNER_THREAD_POOL_MAX_WORKERS`**, **`IRONFLOW_TASK_RUNNER_PROCESS_POOL_MAX_WORKERS`**) are described in **[Runners](../concepts/runners.md)** and **[How to choose a task runner](choose-task-runners.md)**.

## 5. Verify

- Run the **[Quick start (demo flow)](../QUICKSTART_DEMO.md)** (`python python-shim/examples/flow_ironflow.py` with `PYTHONPATH` set).
- Optionally run tests from the repo root:

```bash
python -m pytest python-shim/tests static-planner/tests benchmarks/tests
cargo test --manifest-path rust-engine/Cargo.toml
```

## 6. Install only `prefect_compat` in another project (narrow path)

Preferred path when available: **`pip install ironflow-prefect-compat`** (production PyPI) or use the TestPyPI index pair documented in **[Installation](../INSTALL.md)**.

You can also **`pip install` from Git**:

```bash
python -m pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@v0.1.2#subdirectory=python-shim"
```

That package **does not** ship `rust-engine`; build the native library separately and set **`IRONFLOW_RUST_LIB`**, or accept Python fallbacks. See also **[Installation](../INSTALL.md)** for the same material with slightly different emphasis.
