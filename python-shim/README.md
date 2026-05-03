# ironflow-prefect-compat

**IronFlow** — Prefect-style **`@flow` / `@task`** authoring (`prefect_compat`) backed by a **Rust orchestration kernel** shipped as a **`ctypes`** shared library (`ironflow_engine`).

This PyPI distribution bundles **prebuilt native wheels** per platform where CI publishes them (**CPython 3.11 and 3.12**). You **do not** need Rust installed to **use** those wheels; Rust is used **when building** the package from source (for example **sdist** installs when no wheel matches).

## Install

**Production PyPI** (`pypi.org`) — default install:

```bash
python -m pip install --upgrade pip
python -m pip install ironflow-prefect-compat
```

With **[uv](https://docs.astral.sh/uv/)**:

```bash
uv pip install ironflow-prefect-compat
```

**TestPyPI** (maintainer validation) — use **both** indices so dependencies resolve:

```bash
python -m pip install --upgrade pip
python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  ironflow-prefect-compat
```

## Verify the native library

```bash
python -c "from prefect_compat.rust_bridge import native_library_available; print(native_library_available())"
```

Expect **`True`** when the wheel matched your platform. If **`False`**, set **`IRONFLOW_RUST_LIB`** to a built `ironflow_engine` library or see the full docs.

## Documentation

- **User install & matrices:** [Installation](https://github.com/PPPSDavid/rust-based-prefect/blob/main/docs/INSTALL.md)
- **Hosted docs:** [https://pppsdavid.github.io/rust-based-prefect/](https://pppsdavid.github.io/rust-based-prefect/)
- **Repository / issues:** [github.com/PPPSDavid/rust-based-prefect](https://github.com/PPPSDavid/rust-based-prefect)
- **Compatibility vs Prefect:** [COMPATIBILITY.md](https://github.com/PPPSDavid/rust-based-prefect/blob/main/COMPATIBILITY.md)

## License

Apache-2.0 — see the repository **`LICENSE`** file.
