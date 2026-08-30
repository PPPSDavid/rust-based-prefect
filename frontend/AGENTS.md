# AGENTS — frontend

Ownership: Vite/React UI for runs, DAG, deployments.

Read first: root `AGENTS.md` (Cloud caveats), coordinate API contract changes with `python-shim/`.

## Caveats

- Dev server: open `http://localhost:4173` (IPv6 `localhost`), not `http://127.0.0.1:4173`.
- API calls use `VITE_API_BASE` when set; the Vite dev server proxies `/api` and `/health` to `http://127.0.0.1:8000` (`vite.config.ts`).

## Validate

```bash
npm --prefix frontend run build
# optional e2e when changing run/DAG UX:
# npm --prefix frontend test / playwright per package.json scripts
```

Prefer matching existing UI patterns; do not invent a new design system in drive-by changes.
