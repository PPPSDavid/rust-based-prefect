# Distribution: PyPI, conda, and “one command” installs

**Audience:** maintainers and contributors planning releases. **End users** should follow **[Installation](INSTALL.md)** first.

---

## Current story

| Path | When to use |
| --- | --- |
| **TestPyPI wheels** | Validation installs with **`pip install`** + dual index (see [INSTALL.md](INSTALL.md)); ships **`ironflow_engine`** per platform when a wheel exists for **CPython 3.11**. |
| **GitHub clone + `cargo build`** | Full repo: benchmarks, scripts, optional UI, kernel development. |
| **`pip install git+…#subdirectory=python-shim`** | Python-only integration without TestPyPI; native kernel only if the build ran **`cargo`** or you set **`IRONFLOW_RUST_LIB`**. |

IronFlow is **Rust + Python**. Publishing is closer to **`cryptography`** / **`orjson`** than to a pure-Python package: wheels must **bundle** the **`cdylib`** or users fall back to env overrides / source builds.

## Why it is harder than `pip install prefect`

- **`rust-engine`** ships as a **`cdylib`** loaded with **`ctypes`**. A wheel must **ship** native libraries **per OS/arch/ABI** **or** require users to install Rust (poor “one click” experience).
- CI maintains a **wheel build matrix** (Linux **x86_64** + **aarch64**, Windows **win_amd64**, macOS **universal2**).

## PyPI package layout

1. **Project layout** — **`python-shim/`** is the installable tree; PyPI project name **`ironflow-prefect-compat`**.
2. **Build** — **`python-shim/build_native.py`** runs **`cargo build --release`** against **`rust-engine/`** during **`python -m build --wheel`** when **`cargo`** is on `PATH` and the repo layout is present. Set **`IRONFLOW_SKIP_NATIVE_BUILD=1`** to skip staging the cdylib (pure-Python wheel). **`setup.py`** wires the custom **`build_py`** command (some setuptools versions cannot resolve `cmdclass` from `pyproject.toml` alone).
3. **Install layout** — Platform wheels place **`libironflow_engine.so`** / **`ironflow_engine.dll`** / **`libironflow_engine.dylib`** under **`prefect_compat/native/`** (one filename per wheel).
4. **Runtime** — **`prefect_compat.rust_bridge`** resolves the library in order: **`IRONFLOW_RUST_LIB`** → **repo checkout** (`rust-engine/target/...` when `python-shim` sits next to **`rust-engine`**) → **`importlib.resources`** under **`prefect_compat/native/`** (installed wheel).
5. **Naming** — Version pins align with **`VERSION`** at the repo root and **`rust-engine/Cargo.toml`**.

### Package page metadata checklist (PyPI / TestPyPI)

Project metadata lives in **`python-shim/pyproject.toml`** under **`[project]`** and **`[project.urls]`**:

- **`readme`** — **`python-shim/README.md`** (PyPI long description; setuptools requires paths inside **`python-shim/`**, not the repo root **`README.md`**).
- **`description`** — Short summary line under the package title.
- **`license`** — SPDX **`Apache-2.0`** (matches root **`LICENSE`**). Do **not** also add the deprecated **`License :: OSI Approved :: …`** classifier when using SPDX — recent setuptools rejects the combination.
- **`keywords`** — Discovery (`ironflow`, `prefect`, `orchestration`, `workflow`, `rust`, …).
- **`classifiers`** — Python ABIs, OS families, **`Programming Language :: Rust`** (Rust is a **build/runtime component** of the wheel; it is **not** a pip dependency—there is no **`requirements.txt`** entry for Rust).
- **`urls`** — Homepage, hosted docs, repository, issues, changelog.

Keep **`MANIFEST.in`** in **`python-shim/`** so **`README.md`** / **`LICENSE`** are included in **sdists**.

### CI wheel artifacts

On **`main`** / PRs, these jobs build **`python-shim`**, smoke-install the wheel (**`native_library_available()`**), and upload artifacts:

| Job | Artifact name | Notes |
| --- | --- | --- |
| **`wheel-linux`** | **`ironflow-prefect-compat-wheel-linux`** | **`auditwheel repair`** when possible (**manylinux** tag). |
| **`wheel-linux-aarch64`** | **`ironflow-prefect-compat-wheel-linux-aarch64`** | **QEMU** + **`cibuildwheel`** (aarch64 manylinux). |
| **`wheel-windows`** | **`ironflow-prefect-compat-wheel-windows`** | **`win_amd64`** + **`ironflow_engine.dll`**. |
| **`wheel-macos`** | **`ironflow-prefect-compat-wheel-macos`** | **`macos-latest`** (universal2 / Apple-centric runner — add an Intel-only job if **`x86_64`** wheels are required). |

### TestPyPI (manual)

Workflow **`Publish to TestPyPI`** (**`workflow_dispatch`**) builds **Linux x86_64**, **Linux aarch64**, **Windows**, and **macOS** wheels and uploads them to **https://test.pypi.org** via **`pypa/gh-action-pypi-publish`** (OIDC **trusted publisher** on TestPyPI recommended). Use the **dry run** input to build and download artifacts without uploading. Configure once per **[TestPyPI trusted publishing](https://docs.pypi.org/trusted-publishers/)** (or an API token per PyPA docs).

**Example install** (TestPyPI + main PyPI index for dependencies):

```bash
python -m pip install --upgrade pip
python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  ironflow-prefect-compat
```

## Conda (conda-forge)

- Add a **feedstock** that builds or vendors the same `cdylib` and installs it next to the Python package, or split into `ironflow-engine` (per-platform) + `ironflow-python` (noarch).
- Conda handles non-Python deps well; the work is **recipe maintenance** and **CI** on conda-forge’s infrastructure, not fundamentally different from PyPI’s “ship the `.so`” problem.

## What “one command” can mean

| Goal | Command | Notes |
| --- | --- | --- |
| TestPyPI validation | `pip install` with **TestPyPI** + **pypi.org** extra index (see [INSTALL.md](INSTALL.md)) | Prebuilt **`ironflow_engine`** when a wheel matches **platform + CPython 3.11**. |
| Python API from Git, no local clone of your app | `pip install "git+...@vX.Y.Z#subdirectory=python-shim"` | Native kernel if **`cargo`** ran at build time, else **`IRONFLOW_RUST_LIB`** or local **`cargo build`**. |
| Full stack, no index | Clone + `environment.yml` + `cargo build` | Current path for kernel + benchmarks + UI. |
| Production **PyPI** | `pip install ironflow-prefect-compat` | **Follow-up:** trusted publishing to **pypi.org** (not only TestPyPI). |

## Summary

- **Done in-repo:** **`importlib.resources`** in **`rust_bridge`**, **`prefect_compat/native/`** via **`build_native`**, platform-tagged wheels, **Linux (x86_64 + aarch64) / Windows / macOS** CI, **TestPyPI** publish workflow, and **PyPI-oriented metadata** in **`pyproject.toml`**.
- **Encodes “needs Rust”:** classifiers and docs; **pip** does not install Rust — wheels **embed** the built **`cdylib`**, or document **source build** / **`IRONFLOW_RUST_LIB`**.

### Follow-ups toward production PyPI

- **PyPI.org** publish workflow (mirror **`publish-testpypi.yml`** against **`https://pypi.org`**) and **`RELEASING.md`** checklist.
- Optional: **macOS x86_64-only** job if **`universal2`** is not enough for your support matrix.
