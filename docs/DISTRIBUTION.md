# Distribution: PyPI, conda, and “one command” installs

**Audience:** maintainers and contributors planning releases. **End users** should follow **[Installation](INSTALL.md)** for what is supported **today** (git clone + environment + `cargo build`).

---

Today the smoothest **supported** paths for *using* IronFlow are (also summarized in INSTALL):

- **Full stack:** clone the repo (optionally at a release tag), `conda`/`mamba` or `pip install -r requirements-ci.txt`, then `cargo build` for `rust-engine`. This matches how the project is developed and tested.
- **Python packages only:**  
  `pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@vX.Y.Z#subdirectory=python-shim"`  
  (and optionally `#subdirectory=static-planner`). That is already a **single `pip` command**, but it does **not** include a prebuilt **`rust-engine`** binary; see below.

There is **no** `pip install ironflow` on PyPI or `conda install ironflow` on conda-forge **yet**. Adding them is possible and would look like Prefect’s story in outline, but IronFlow is **Rust + Python**, so publishing is more like `cryptography` or `orjson` than a pure-Python package.

## Why it is harder than `pip install prefect`

- **`rust-engine`** ships as a **`cdylib`** loaded with **`ctypes`** from paths under the repo (or `IRONFLOW_RUST_LIB`). A PyPI wheel must **ship those native libraries inside the wheel** (per OS/arch/ABI) **or** require users to install Rust and compile (poor “one click” experience).
- You need a **wheel build matrix** (Linux manylinux, macOS arm64/x86_64, Windows) and CI that runs `cargo build --release` for each target, then packages the artifact next to `prefect_compat`.

## PyPI (realistic shape)

1. **Project layout**  
   - Keep `python-shim/` as the installable tree (or a thin wrapper package at the repo root that depends on it).
2. **Build**  
   - Use **[Maturin](https://www.maturin.rs/)** (common for Rust+Python) or **setuptools-rust** to drive `cargo build --release` during `pip wheel` / CI.
3. **Install layout**  
   - Place `libironflow_engine.so` / `.dll` / `.dylib` under something like `prefect_compat/native/<platform>/` in the wheel.
4. **Runtime**  
   - Extend `prefect_compat.rust_bridge._candidate_lib_paths()` to resolve the library via **`importlib.resources`** / **`importlib.resources.files("prefect_compat")`** so loading works from `site-packages`, not only from a git checkout.
5. **Naming**  
   - Reserve a PyPI name (e.g. `ironflow` or `ironflow-prefect-compat`) and publish **version pins** aligned with `VERSION` in this repo.

After that, users get:

```bash
pip install ironflow   # hypothetical package name
```

with the native library bundled for their platform (when a wheel exists), or a clear error / source-build path.

## Conda (conda-forge)

- Add a **feedstock** that builds or vendors the same `cdylib` and installs it next to the Python package, or split into `ironflow-engine` (per-platform) + `ironflow-python` (noarch).
- Conda handles non-Python deps well; the work is **recipe maintenance** and **CI** on conda-forge’s infrastructure, not fundamentally different from PyPI’s “ship the `.so`” problem.

## What “one click” can mean in the meantime

| Goal | Command | Notes |
| --- | --- | --- |
| Python API only, no local git | `pip install "git+...@vX.Y.Z#subdirectory=python-shim"` | Already one line; build Rust separately if you need the kernel. |
| Full stack without PyPI | Clone + `environment.yml` + `cargo build` | Current recommended path for kernel + benchmarks + UI. |
| Future | `pip install ironflow` | Needs wheel pipeline + `rust_bridge` install layout above. |

## Summary

- **Yes:** PyPI and conda are standard ways to get to a **single install command**, similar in *user experience* to Prefect, but IronFlow must **bundle or build the Rust `cdylib`** and teach the loader to find it inside an installed package.
- **Easiest next step toward PyPI:** add **`importlib.resources`-based** discovery in `rust_bridge.py` and a **manual or CI-built wheel** for one platform to prove the layout; then expand the build matrix.

If you want this tracked as work, open an issue titled “PyPI wheels + rust_bridge package resources” and link to this file.
