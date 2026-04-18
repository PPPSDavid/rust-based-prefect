# How to run the server and UI

IronFlow can run flows **in-process** with no server. When you want HTTP APIs and an optional local dashboard, use the bundled launcher.

## API backend

The FastAPI app lives at **`python-shim.src.prefect_compat.server:app`**. From the **repository root**, with dependencies installed:

```bash
python -m uvicorn python-shim.src.prefect_compat.server:app --host 127.0.0.1 --port 8000
```

## API + UI helper

**`scripts/ironflow_server.py`** starts the backend and, by default, the **Vite** frontend under `frontend/`:

```bash
python scripts/ironflow_server.py start
```

Defaults: backend **`127.0.0.1:8000`**, frontend **`http://localhost:4173`**. Useful flags:

- **`--backend-only`** — API only (no Node/npm).
- **`--host`**, **`--backend-port`**, **`--frontend-port`** — adjust bind addresses and ports.

The helper runs **`npm install`** and **`npm run dev`** in `frontend/` when the UI is enabled, so **Node.js** and **npm** must be on `PATH` unless you use **`--backend-only`**.

## Verify the UI

Use **[Optional: verify the web UI](../ui_e2e_visual_check.md)** for a quick visual smoke check once services are up.
