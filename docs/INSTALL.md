# Installation

IronFlow can be used in three ways: **prebuilt wheels** (recommended when available for your platform), **`pip` from Git** (Python packages only), or a **full source checkout** (kernel, benchmarks, UI). The Rust **`ironflow_engine`** shared library is **bundled inside platform wheels** when you install a compatible wheel; you **do not** need the Rust toolchain on the machine in that case.

## Prerequisites

| Requirement | Notes |
| --- | --- |
| **Git** | To clone and update the repository (source / git-install paths). |
| **Python 3.11+** | `environment.yml` in the repo pins 3.12; 3.11 matches CI. |
| **Rust toolchain** | Needed only for **source** workflows: building `rust-engine/` from a checkout, or building the shim when **`cargo`** runs during an sdist/wheel build. **Not** required when you install a **prebuilt wheel** that includes `prefect_compat/native/*`. |
| **Conda or venv** | Either is fine; conda is what the repo’s `environment.yml` is written for. |

Optional: **Node.js** only if you want the Vite UI (`frontend/`). The API and flows do not require Node.

## Quick check after any install

From an environment where `prefect_compat` is installed:

```bash
python -c "from prefect_compat.rust_bridge import native_library_available; print('native_library_available=', native_library_available())"
```

You want **`native_library_available=True`** when using the intended Rust-backed path. If it is **`False`**, see **`IRONFLOW_RUST_LIB`** and [how-to/setup.md](how-to/setup.md).

---

## 1. Prebuilt wheels (`ironflow-prefect-compat`)

**Package name:** `ironflow-prefect-compat`  
Maintainers may publish to **TestPyPI** (validation) and/or **production PyPI**; see [Distribution](https://github.com/PPPSDavid/rust-based-prefect/blob/main/docs/DISTRIBUTION.md) and [Releasing](https://github.com/PPPSDavid/rust-based-prefect/blob/main/RELEASING.md) (maintainer-oriented files; not part of the hosted MkDocs site).

Wheels are built for **CPython 3.11** on:

| Platform | Typical wheel tag |
| --- | --- |
| Linux x86_64 | `manylinux_*_x86_64` |
| Linux aarch64 (e.g. Raspberry Pi 64-bit) | `manylinux_*_aarch64` |
| Windows x86_64 | `win_amd64` |
| macOS (universal2 from CI) | `macosx_*_universal2` |

### Production PyPI (pypi.org)

When wheels are available on the default index:

```bash
python -m pip install --upgrade pip
python -m pip install ironflow-prefect-compat
```

Then run the **quick check** at the top of this page.

### TestPyPI (validation index)

TestPyPI does not mirror all upstream dependencies. Install with **both** TestPyPI and the real PyPI index so transitive packages resolve:

```bash
python -m pip install --upgrade pip
python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  ironflow-prefect-compat
```

Then run the **quick check** above.

**Windows (conda example):**

```powershell
conda create -n ironflow-testpypi python=3.11 -y
conda activate ironflow-testpypi
python -m pip install --upgrade pip
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ ironflow-prefect-compat
python -c "from prefect_compat.rust_bridge import native_library_available; print('native_library_available=', native_library_available())"
```

---

## 2. Get the code (full stack / development)

Pick a **[release tag](https://github.com/PPPSDavid/rust-based-prefect/releases)** for a stable snapshot, or use `main` for the latest development state.

```bash
git clone https://github.com/PPPSDavid/rust-based-prefect.git
cd rust-based-prefect
git checkout v0.1.1   # replace with current release tag, or omit to stay on main
```

## 3. Python environment

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

## 4. Build the Rust engine (source checkout)

From the **repository root**:

```bash
cargo build --manifest-path rust-engine/Cargo.toml
```

Release builds are typical for day-to-day use:

```bash
cargo build --release --manifest-path rust-engine/Cargo.toml
```

The Python shim looks for the `ironflow_engine` shared library under `rust-engine/target/` (or use **`IRONFLOW_RUST_LIB`** to point at a specific file). Without a successful build, some paths fall back to Python implementations where provided; for the intended behavior from source, treat this step as **part of a normal install**, not an optional extra.

## 5. Check that it works (from repo root)

Canonical bootstrap flow:

```bash
python scripts/bootstrap.py --check-only
python scripts/bootstrap.py
```

Use `--check-only` for fast environment validation when you do not want to build and run smoke checks yet.

Run the **[Quick start demo](QUICKSTART_DEMO.md)** (sets `PYTHONPATH` and runs `python-shim/examples/flow_ironflow.py`). You should see `ironflow_result=26` and an event count printed.

Optionally run the test suites from the repo root:

```bash
python -m pytest python-shim/tests static-planner/tests benchmarks/tests
cargo test --manifest-path rust-engine/Cargo.toml
```

## 6. Install only the Python packages (narrow use)

If you need `prefect_compat` inside **another project** without cloning the full tree, you can install the shim **from Git**:

```bash
python -m pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@v0.1.1#subdirectory=python-shim"
```

Replace the tag with your target release. This install **does not** compile Rust unless **`cargo`** is available during the pip build; otherwise build **`rust-engine`** separately and set **`IRONFLOW_RUST_LIB`**, or accept Python fallbacks where implemented. For the **static planner** package:

```bash
python -m pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@v0.1.1#subdirectory=static-planner"
```

## 7. Optional: API and UI

After the above, you can start the bundled HTTP server and UI — see **[How to run the server and UI](how-to/server-and-ui.md)** or the repository **README** (`scripts/ironflow_server.py`, `uvicorn`, and `frontend/`). These are optional for running flows in-process.

## See also

- **[How to set up IronFlow](how-to/setup.md)** — condensed setup and environment variables in one place.
- **[Distribution](https://github.com/PPPSDavid/rust-based-prefect/blob/main/docs/DISTRIBUTION.md)** — maintainer notes on wheels, CI, TestPyPI vs production PyPI.

## What is not available yet

- **conda-forge** packages with prebuilt native libraries are **not** published yet.
- If **`pip install ironflow-prefect-compat`** fails (no matching wheel yet), use **TestPyPI**, **git install**, or a **full checkout** + **`cargo build`** as described above.
