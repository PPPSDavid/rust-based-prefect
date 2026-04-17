# How to set up IronFlow

This guide consolidates the **supported install path** from source: Python environment, **Rust** `rust-engine` build, and the environment variables the shim respects. For a minimal end-to-end check after setup, see **[Quick start (demo flow)](../QUICKSTART_DEMO.md)**.

## 1. Get the code

Pick a **[release tag](https://github.com/PPPSDavid/rust-based-prefect/releases)** for stability, or use `main` for the latest development state.

```bash
git clone https://github.com/PPPSDavid/rust-based-prefect.git
cd rust-based-prefect
git checkout v0.1.1   # optional: replace with current tag
```

## 2. Python environment

**Conda (recommended; matches maintainers’ stack)**

```bash
mamba env create -f environment.yml    # or: conda env create -f environment.yml
conda activate ironflow-dev
```

**`venv` + pip (no conda)**

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-ci.txt
```

Use **Python 3.11+** (`environment.yml` may pin 3.12).

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
| **`IRONFLOW_HISTORY_PATH`** | When set to a file path, flow history can be **appended as JSONL** for inspection and tooling (see the repository README for defaults and behavior). |
| **`PYTHONPATH`** | Set to `python-shim/src` at the repo root so `import prefect_compat` works **without** an editable install (used in **[Quick start](../QUICKSTART_DEMO.md)**). |

Task-runner–related optional variables (**`IRONFLOW_TASK_RUNNER`**, **`IRONFLOW_TASK_RUNNER_THREAD_POOL_MAX_WORKERS`**, **`IRONFLOW_TASK_RUNNER_PROCESS_POOL_MAX_WORKERS`**) are described in **[Runners](../concepts/runners.md)**.

## 5. Verify

- Run the **[Quick start (demo flow)](../QUICKSTART_DEMO.md)** (`python python-shim/examples/flow_ironflow.py` with `PYTHONPATH` set).
- Optionally run tests from the repo root:

```bash
python -m pytest python-shim/tests static-planner/tests benchmarks/tests
cargo test --manifest-path rust-engine/Cargo.toml
```

## 6. Install only `prefect_compat` in another project (narrow path)

You can **`pip install` from Git** (still **no PyPI wheel** for the full stack):

```bash
python -m pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@v0.1.1#subdirectory=python-shim"
```

That package **does not** ship `rust-engine`; build the native library separately and set **`IRONFLOW_RUST_LIB`**, or accept Python fallbacks. See also **[Installation](../INSTALL.md)** for the same material with slightly different emphasis.
