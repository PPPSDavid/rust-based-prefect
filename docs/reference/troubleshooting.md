# Troubleshooting and FAQ

Common issues when installing IronFlow, running flows, or operating the self-hosted API.

## Install and native library

### `native_library_available()` returns `False`

**Symptoms:** Quick check prints `False`; flows may use Python fallbacks.

**Checks:**

1. Confirm Python **3.11–3.14** on a [supported platform](../INSTALL.md) (wheels bundle the Rust engine).
2. Reinstall: `python -m pip install --upgrade --force-reinstall ironflow-prefect-compat`
3. If you built from source, run `cargo build --manifest-path rust-engine/Cargo.toml` and set `IRONFLOW_RUST_LIB` to the `cdylib` path.
4. See [Environment variables](env-vars.md) for `IRONFLOW_RUST_LIB` and `IRONFLOW_USE_RUST_FSM`.

### `pip` installs sdist and fails (needs Rust)

**Cause:** No prebuilt wheel for your Python version or platform (e.g. CPython 3.15+, or free-threaded 3.14t on production PyPI).

**Fix:** Use CPython **3.11–3.14** (GIL build), or clone the repo and build `rust-engine` before installing. For experimental **3.14t**, use TestPyPI / CI artifacts as in [Installation](../INSTALL.md).

### `ironflow: command not found`

**Cause:** Console scripts are not on `PATH` (common in some virtualenv setups).

**Fix:** Run as a module or use the full path:

```bash
python -m prefect_compat.cli.main --help
```

Or reinstall with `python -m pip install ironflow-prefect-compat` inside an activated venv.

## Server and deployments

### Deployment runs stay `PENDING` / never start

**Checks:**

1. API is running: `curl http://127.0.0.1:8000/health`
2. Embedded worker enabled: `IRONFLOW_ENABLE_LOCAL_WORKER` is not `0`/`false`/`no`.
3. If using a **standalone worker**, server must have `IRONFLOW_ENABLE_LOCAL_WORKER=0` and worker must share `IRONFLOW_HISTORY_PATH`.
4. Work pool name in `ironflow.yaml` matches an existing pool (default: `default-process-pool`).

### `ironflow deploy` cannot reach API

**Checks:**

1. `IRONFLOW_API_URL` matches the running server (default `http://127.0.0.1:8000`).
2. Start API with `python -m uvicorn prefect_compat.server:app --host 127.0.0.1 --port 8000`.

### History mismatch between CLI and server

**Cause:** Different `IRONFLOW_HISTORY_PATH` values in separate terminals.

**Fix:** Export the same path before starting the server and any standalone worker:

```bash
export IRONFLOW_HISTORY_PATH="$(pwd)/data/ironflow_history.jsonl"
```

## Web UI

### UI does not load at `http://127.0.0.1:4173`

**Cause:** Vite dev server binds to IPv6 `localhost` only.

**Fix:** Open **`http://localhost:4173`** (not `127.0.0.1`). See [Verify the web UI](../ui_e2e_visual_check.md).

### UI shows no runs

**Checks:**

1. Backend at `http://127.0.0.1:8000` is up.
2. Seed or trigger runs against the same `IRONFLOW_HISTORY_PATH` as the server.
3. UI requires a **repository clone** (`frontend/`); it is not shipped on PyPI.

## Flow authoring

### `ImportError: No module named prefect_compat`

**Fix (repo):** `pip install -e python-shim` or `export PYTHONPATH=python-shim/src`.

**Fix (PyPI):** `pip install ironflow-prefect-compat`.

### Behavior differs from Prefect

IronFlow implements a **subset** of Prefect 3.x. Check [Compatibility matrix](../compatibility.md) and [Prefect → IronFlow](../PREFECT_IRONFLOW_MAPPING.md) before porting.

## Getting help

- [Compatibility matrix](../compatibility.md) — supported features.
- [REST API overview](api.md) — HTTP errors and endpoints.
- [GitHub Issues](https://github.com/PPPSDavid/rust-based-prefect/issues) — bugs and feature requests.
