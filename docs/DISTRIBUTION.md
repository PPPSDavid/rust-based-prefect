# Distribution: PyPI, conda, and “one command” installs

**Audience:** maintainers and contributors planning releases. **End users** should follow **[Installation](INSTALL.md)** for what is supported **today** (git clone + environment + `cargo build`).

---

Today the smoothest **supported** paths for *using* IronFlow are (also summarized in INSTALL):

- **Full stack:** clone the repo (optionally at a release tag), `conda`/`mamba` or `pip install -r requirements-ci.txt`, then `cargo build` for `rust-engine`. This matches how the project is developed and tested.
- **Python packages only:**  
  `pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@vX.Y.Z#subdirectory=python-shim"`  
  (and optionally `#subdirectory=static-planner`). That is already a **single `pip` command**. When that install is built **from a checkout that ran `cargo build --release` during packaging**, the wheel can bundle **`libironflow_engine.so`** / **`ironflow_engine.dll`** under `prefect_compat/native/` (see runtime loader in `prefect_compat.rust_bridge`). **Git installs from GitHub source alone** still do not compile Rust for you unless your environment supplies **`cargo`** during the pip build or you build **`rust-engine`** separately and set **`IRONFLOW_RUST_LIB`**.

There is **no** `pip install ironflow` on PyPI or `conda install ironflow` on conda-forge **yet**. Adding them is possible and would look like Prefect’s story in outline, but IronFlow is **Rust + Python**, so publishing is more like `cryptography` or `orjson` than a pure-Python package.

## Why it is harder than `pip install prefect`

- **`rust-engine`** ships as a **`cdylib`** loaded with **`ctypes`** from paths under the repo (or `IRONFLOW_RUST_LIB`). A PyPI wheel must **ship those native libraries inside the wheel** (per OS/arch/ABI) **or** require users to install Rust and compile (poor “one click” experience).
- You need a **wheel build matrix** (Linux manylinux, macOS arm64/x86_64, Windows) and CI that runs `cargo build --release` for each target, then packages the artifact next to `prefect_compat`.

## PyPI (realistic shape)

1. **Project layout**  
   - Keep `python-shim/` as the installable tree (package name **`ironflow-prefect-compat`** on PyPI when published).
2. **Build**  
   - **`python-shim/build_native.py`** runs **`cargo build --release`** against **`rust-engine/`** during **`python -m build --wheel`** when **`cargo`** is on `PATH` and the repo layout is present. Set **`IRONFLOW_SKIP_NATIVE_BUILD=1`** to skip staging the cdylib (pure-Python wheel). **`setup.py`** wires the custom **`build_py`** command (some setuptools versions cannot resolve `cmdclass` from `pyproject.toml` alone).
3. **Install layout**  
   - Platform wheels place **`libironflow_engine.so`** / **`ironflow_engine.dll`** / **`libironflow_engine.dylib`** under **`prefect_compat/native/`** (one filename per wheel).
4. **Runtime**  
   - **`prefect_compat.rust_bridge`** resolves the library in order: **`IRONFLOW_RUST_LIB`** → **repo checkout** (`rust-engine/target/...` when `python-shim` is next to `rust-engine`) → **`importlib.resources`** under **`prefect_compat/native/`** (installed wheel).
5. **Naming**  
   - Reserve a PyPI name (e.g. `ironflow` or `ironflow-prefect-compat`) and publish **version pins** aligned with `VERSION` in this repo.

### CI wheel artifacts

On **`main`** / PRs, **`wheel-linux`**, **`wheel-windows`**, and **`wheel-macos`** each build **`python-shim`**, smoke-install the wheel ( **`native_library_available()`** ), and upload artifacts:

- **`ironflow-prefect-compat-wheel-linux`** — **`auditwheel repair`** when possible (manylinux tag).
- **`ironflow-prefect-compat-wheel-windows`** — Windows **`win_*`** wheel with **`ironflow_engine.dll`**.
- **`ironflow-prefect-compat-wheel-macos`** — Apple Silicon runners today (**`macos-latest`**); add an Intel macOS job later if you need **`x86_64`** wheels.

### TestPyPI (manual)

Workflow **`Publish to TestPyPI`** ( **`workflow_dispatch`** ) builds the same three wheels and uploads them to **https://test.pypi.org** using **`pypa/gh-action-pypi-publish`** (OIDC **trusted publisher** on TestPyPI recommended). Use optional **dry run** to build and download artifacts without uploading. Configure once per **[TestPyPI trusted publishing](https://docs.pypi.org/trusted-publishers/)** for this repository (or use an API token per upstream docs).

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
| Python API only, no local git | `pip install "git+...@vX.Y.Z#subdirectory=python-shim"` | Already one line; includes native kernel **when** the wheel/sdist build ran **`cargo`** against **`rust-engine`** (CI artifact), else set **`IRONFLOW_RUST_LIB`** or build locally. |
| Full stack without PyPI | Clone + `environment.yml` + `cargo build` | Current recommended path for kernel + benchmarks + UI. |
| PyPI | `pip install ironflow-prefect-compat` | **Not published yet**; loader + Linux CI wheel pipeline are in-repo — publishing is wiring **Trusted Publishing** + release workflow. |

## Summary

- **Yes:** PyPI and conda are standard ways to get to a **single install command**, similar in *user experience* to Prefect, but IronFlow must **bundle or build the Rust `cdylib`** and teach the loader to find it inside an installed package.
- **Done in-repo:** **`importlib.resources`** discovery in **`rust_bridge`**, **`prefect_compat/native/`** staging via **`build_native`**, platform-tagged wheels when a cdylib is present, **Linux / Windows / macOS** wheel CI jobs, and a **manual TestPyPI** publish workflow.

### Follow-ups toward PyPI publishing

- Production **PyPI** workflow (mirror **`publish-testpypi.yml`** against **`pypi.org`**) and release checklist updates.
- Optional extra wheels: **Linux aarch64**, **macOS x86_64** runners, or fat/universal macOS strategy if demand appears.
