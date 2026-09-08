# AGENTS — frontend

Ownership: Vite/React UI for runs, DAG, deployments.

Read first: root `AGENTS.md` (Cloud caveats), coordinate API contract changes with `python-shim/`.
Roadmap: `docs/plans/ui-parity-roadmap.md` (U0–U6).

## Design system (U0+)

- Tokens live in `src/styles/tokens.css` (CSS variables for light/dark + state palette).
- Primitives live in `src/components/ui/` (shadcn-style, Radix-backed). Prefer composing these over new one-off CSS.
- Utility helper: `src/lib/utils.ts` → `cn()`.
- Theme: `src/hooks/useTheme.ts` (`localStorage` key `ironflow-theme`: `light` | `dark` | `system`).
- Legacy page CSS remains in `src/styles.css` until U1 finishes the page-by-page migration; prefer tokens there too.
- Do **not** invent a parallel design system — extend tokens / `components/ui`.

## Caveats

- Dev server: open `http://localhost:4173` (IPv6 `localhost`), not `http://127.0.0.1:4173`.
- API calls use `VITE_API_BASE` when set; the Vite dev server proxies `/api` and `/health` to `http://127.0.0.1:8000` (`vite.config.ts`).

## Validate

```bash
npm --prefix frontend run build   # includes bundle-budget check
npm --prefix frontend test
# optional e2e when changing run/DAG UX:
# npm --prefix frontend run test:e2e
```
