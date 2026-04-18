# Installation

IronFlow is installed **from source today**: clone the GitHub repository, create a Python environment, and build the **Rust** `rust-engine` native library. There is **no** `pip install ironflow` package on PyPI yet; the supported paths below reflect **what works now**.

## Prerequisites

| Requirement | Notes |
| --- | --- |
| **Git** | To clone and update the repository. |
| **Python 3.11+** | `environment.yml` in the repo pins 3.12; 3.11 matches CI. |
| **Rust toolchain** | `rustup` with stable `cargo` — needed to build `rust-engine/` (the orchestration kernel). |
| **Conda or venv** | Either is fine; conda is what the repo’s `environment.yml` is written for. |

Optional: **Node.js** only if you want the Vite UI (`frontend/`). The API and flows do not require Node.

## 1. Get the code

Pick a **[release tag](https://github.com/PPPSDavid/rust-based-prefect/releases)** for a stable snapshot, or use `main` for the latest development state.

```bash
git clone https://github.com/PPPSDavid/rust-based-prefect.git
cd rust-based-prefect
git checkout v0.1.1   # replace with current release tag, or omit to stay on main
```

## 2. Python environment

**Option A — Conda (recommended, matches maintainers’ stack)**

```bash
mamba env create -f environment.yml    # or: conda env create -f environment.yml
conda activate ironflow-dev
```

**Option B — `venv` + pip (no conda)**

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-ci.txt
```

`requirements-ci.txt` lists the Python packages needed to run tests and the shim; it does not install Prefect unless you add it yourself (the conda env pulls Prefect for benchmarks via `environment.yml`).

## 3. Build the Rust engine

From the **repository root**:

```bash
cargo build --manifest-path rust-engine/Cargo.toml
```

Release builds are typical for day-to-day use:

```bash
cargo build --release --manifest-path rust-engine/Cargo.toml
```

The Python shim looks for the `ironflow_engine` shared library under `rust-engine/target/` (or use **`IRONFLOW_RUST_LIB`** to point at a specific file). Without a successful build, some paths fall back to Python implementations where provided; for the intended behavior, treat this step as **part of a normal install**, not an optional extra.

## 4. Check that it works

Run the **[Quick start demo](QUICKSTART_DEMO.md)** (sets `PYTHONPATH` and runs `python-shim/examples/flow_ironflow.py`). You should see `ironflow_result=26` and an event count printed.

Optionally run the test suites from the repo root:

```bash
python -m pytest python-shim/tests static-planner/tests benchmarks/tests
cargo test --manifest-path rust-engine/Cargo.toml
```

## 5. Install only the Python packages (narrow use)

If you need `prefect_compat` inside **another project** without cloning the full tree, you can install the shim **from Git** (still **no PyPI wheel** today):

```bash
python -m pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@v0.1.1#subdirectory=python-shim"
```

Replace the tag with your target release. This install **does not** include `rust-engine`; build it separately and set **`IRONFLOW_RUST_LIB`**, or accept Python fallbacks where implemented. For the **static planner** package:

```bash
python -m pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@v0.1.1#subdirectory=static-planner"
```

## 6. Optional: API and UI

After the above, you can start the bundled HTTP server and UI — see **[How to run the server and UI](how-to/server-and-ui.md)** or the repository **README** (`scripts/ironflow_server.py`, `uvicorn`, and `frontend/`). These are optional for running flows in-process.

## See also

- **[How to set up IronFlow](how-to/setup.md)** — condensed setup and environment variables in one place.

## What is not available yet

- **PyPI / conda-forge packages** with prebuilt native libraries are **not** published. Installing means **git + environment + `cargo build`** as above, or **pip-from-git** for Python-only with the limitations noted.
- Contributor-facing notes on future packaging live in the repository file `docs/DISTRIBUTION.md` (not required reading to use IronFlow).
