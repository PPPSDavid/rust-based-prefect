# Installation

!!! tip "Quick install (PyPI)"
    ```bash
    python -m pip install --upgrade pip
    python -m pip install ironflow-prefect-compat
    ```
    Requires **CPython 3.11 or 3.12** (see wheel platform table below). Then run the [Quick start: PyPI](QUICKSTART_PYPI.md) (no clone) or [Quickstart: first deployment](quickstart-first-deployment.md).

**Primary path:** install **`ironflow-prefect-compat`** from **PyPI** with **`pip`** or **`uv`** — the same workflow as other wheel-published Python packages. On supported platforms, **prebuilt wheels bundle** the Rust **`ironflow_engine`** library under `prefect_compat/native/`; you **do not** need a Rust toolchain for those wheels.

**Secondary paths:** install **from Git** (narrow Python-only integration) or **clone the repository** to build **`rust-engine`** from source (development, benchmarks, optional UI, or when no wheel matches your platform/Python ABI).

## Prerequisites

| Requirement | Notes |
| --- | --- |
| **Git** | To clone and update the repository (source / git-install paths). |
| **Python 3.11+** | PyPI ships **wheels for 3.11 and 3.12**; `environment.yml` pins 3.12 for the full dev stack. |
| **Rust toolchain** | Needed only for **source** workflows: building `rust-engine/` from a checkout, or building the shim when **`cargo`** runs during an sdist/wheel build. **Not** required when you install a **prebuilt wheel** that includes `prefect_compat/native/*`. |
| **Conda or venv** | Either is fine; conda is what the repo’s `environment.yml` is written for. |

Optional: **Node.js** only if you want the Vite UI (`frontend/`). The API and flows do not require Node.

## Quick check after any install

From an environment where `prefect_compat` is installed:

```bash
python -c "from prefect_compat.rust_bridge import native_library_available; print('native_library_available=', native_library_available())"
```

If you have a **repository checkout** (so `scripts/` is on disk), you can run the same checks through **`scripts/bootstrap.py`**:

- **PyPI / wheel-style check** (no `pytest`, `cargo`, or repo-root layout required; works from any working directory as long as the package is importable):

```bash
python scripts/bootstrap.py --native-check
```

- **Repository development check** (toolchain diagnostics only; full bootstrap is in **[§5](#5-check-that-it-works-from-repo-root)**):

```bash
python scripts/bootstrap.py --check-only
```

If you installed **only** the wheel and do not have `scripts/bootstrap.py`, use the **`python -c`** one-liner above.

You want **`native_library_available=True`** when using the intended Rust-backed path. If it is **`False`**, see **`IRONFLOW_RUST_LIB`** and [how-to/setup.md](how-to/setup.md).

---

## 1. PyPI — `pip` / `uv` (`ironflow-prefect-compat`)

**Package name:** `ironflow-prefect-compat` — **PyPI:** [`ironflow-prefect-compat`](https://pypi.org/project/ironflow-prefect-compat/) · **`requires-python`:** `>=3.11` (see `python-shim/pyproject.toml`).

Maintainers may publish to **TestPyPI** (validation) and/or **production PyPI**; see [Distribution](https://github.com/PPPSDavid/rust-based-prefect/blob/main/docs/DISTRIBUTION.md) and [Releasing](https://github.com/PPPSDavid/rust-based-prefect/blob/main/RELEASING.md) (maintainer-oriented files; not part of the hosted MkDocs site).

Prebuilt wheels are published for **CPython 3.11 and 3.12** on:

| Platform | Typical wheel tag |
| --- | --- |
| Linux x86_64 | `manylinux_*_x86_64` · `cp311` / `cp312` |
| Linux aarch64 (e.g. Raspberry Pi 64-bit) | `manylinux_*_aarch64` · `cp311` / `cp312` |
| Windows x86_64 | `win_amd64` · `cp311` / `cp312` |
| macOS (universal2 from CI) | `macosx_*_universal2` · `cp311` / `cp312` |

**CPython 3.13 and newer** are not guaranteed to have prebuilt wheels yet; **`pip`** / **`uv`** may fall back to **sdist** (needs Rust/`cargo` during install) or fail until wheels exist—use **3.11** or **3.12** for the smoothest install, or a full checkout + `cargo build`.

Other Python versions may install from **sdist** or need a **source build**; check PyPI “Download files” or use a full checkout + `cargo build`.

### Production PyPI (pypi.org)

```bash
python -m pip install --upgrade pip
python -m pip install ironflow-prefect-compat
```

With **[uv](https://docs.astral.sh/uv/)** (after [`uv`](https://docs.astral.sh/uv/getting-started/installation/) is installed):

```bash
uv pip install ironflow-prefect-compat
```

Then run the **quick check** above.

### Wheels vs source builds (honest limits)

Use the **platform matrix** above. Confirm filenames for your platform on PyPI → **Download files**.

Maintainer packaging notes: [Distribution](https://github.com/PPPSDavid/rust-based-prefect/blob/main/docs/DISTRIBUTION.md) and [Releasing](https://github.com/PPPSDavid/rust-based-prefect/blob/main/RELEASING.md) (not published on the hosted MkDocs site).

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
git checkout v0.2.0   # replace with current release tag, or omit to stay on main
```

## 3. Python environment

**Option A — uv (recommended; matches CI / Cursor Cloud)**

```bash
# Install uv: https://docs.astral.sh/uv/getting-started/installation/
uv sync --group dev
```

Uses the root workspace and committed `uv.lock`. Prefer `uv run pytest …` afterward.

**Option B — Conda (optional full desktop stack)**

```bash
mamba env create -f environment.yml    # or: conda env create -f environment.yml
conda activate ironflow-dev
```

**Option C — `venv` + pip (transitional, no uv)**

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-ci.txt
```

`requirements-ci.txt` mirrors `[dependency-groups].dev` in the root `pyproject.toml` for pip-only environments; it does not install Prefect unless you add it yourself (the conda env pulls Prefect for benchmarks via `environment.yml`).

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

Prefer **`pip install ironflow-prefect-compat`** or **`uv pip install ironflow-prefect-compat`** from PyPI when a wheel matches your platform (see **[§1](#1-pypi-pip-uv-ironflow-prefect-compat)**). If you need a **Git URL** pin instead (pre-release testing or fork), install the shim **from Git**:

```bash
python -m pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@v0.2.0#subdirectory=python-shim"
```

Replace the tag with your target release. This install **does not** compile Rust unless **`cargo`** is available during the pip build; otherwise build **`rust-engine`** separately and set **`IRONFLOW_RUST_LIB`**, or accept Python fallbacks where implemented. For the **static planner** package:

```bash
python -m pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@v0.2.0#subdirectory=static-planner"
```

## 7. Optional: API and UI

After the above, you can start the bundled HTTP server and UI — see **[How to run the server and UI](how-to/server-and-ui.md)** or the repository **README** (`scripts/ironflow_server.py`, `uvicorn`, and `frontend/`). These are optional for running flows in-process.

## See also

- **[How to set up IronFlow](how-to/setup.md)** — condensed setup and environment variables in one place.
- **[Quick start (demo flow)](QUICKSTART_DEMO.md)** — bundled `flow_ironflow.py` after a clone + `cargo build`.
- **[Distribution](https://github.com/PPPSDavid/rust-based-prefect/blob/main/docs/DISTRIBUTION.md)** — maintainer notes on wheels, CI, TestPyPI vs production PyPI.

## What is not available yet

- **conda-forge** packages with prebuilt native libraries are **not** published yet.
- If **`pip install ironflow-prefect-compat`** fails (no matching wheel yet), use **TestPyPI**, **git install**, or a **full checkout** + **`cargo build`** as described above.
