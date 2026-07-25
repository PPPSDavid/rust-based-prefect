# UI ops experience & aesthetics (iterative)

**Status:** Priority track accepted — implement in short visual/UX passes with maintainer confirm  
**Canvas ID:** **PU** (parallel to P3/P4; not Prefect API parity)  
**Last updated:** 2026-07-25  
**Ownership:** `frontend/` (+ thin API gaps in `python-shim/` only when lists/filters need them)  
**Related:** `docs/ui_prefect_parity_checklist.md` (functional parity; often stale), `docs/ui_e2e_visual_check.md`

---

## 1. Why this is prioritized

IronFlow’s UI already covers the main nav surfaces (runs, flows, deployments, work pools, run detail + DAG). Gaps called out in review:

1. **Aesthetics lag** — current shell feels like an early dark prototype (default stack/Inter, dense but not intentional; little visual hierarchy).
2. **Ops UX for real workload** — monitoring / maintaining / operating **hundreds** of flows and runs is not yet designed (search, filters, density, bulk actions, “what needs attention”, keyboard, empty/error states at scale).

This track is **product UX**, not “clone Prefect pixels” (still an explicit non-goal).

---

## 2. Working method (render → confirm → next)

Do **not** ship a big-bang redesign. Each session is one pass:

```text
1. Agent implements a narrow UI slice (tokens / one page / one ops pattern)
2. Agent runs API + UI, seeds representative data (incl. high-cardinality where needed)
3. Agent captures screenshots (and optional short screen recording) of before/after
4. Maintainer reviews artifacts and replies: Approve / Tweak / Reject direction
5. Only then open the next pass
```

**Session brief template** (paste into each PU agent task):

- Goal / pass ID (e.g. `PU.1`)
- In scope pages/components
- Out of scope
- Seed script or data volume target (e.g. “≥200 flow runs, mixed states”)
- Artifacts required: screenshots under `/opt/cursor/artifacts/…` or `docs/` only if intentionally committed
- Stop after render; **do not** start the next pass without confirm

---

## 3. Design principles (ops console)

| Principle | Meaning |
| --- | --- |
| One job per view | List = find & triage; detail = act & diagnose |
| Scan first | State, name, time, failure reason readable in &lt;1s at dense scale |
| Progressive disclosure | Advanced filters/DAG chrome secondary; primary actions obvious |
| Intentional visuals | Pick a clear IronFlow direction (tokens); avoid “AI default” purple glow / generic Inter-on-slate without a chosen brand |
| Preserve DAG strength | Logical/expanded DAG is a differentiator — polish, don’t bury |
| No Prefect clone | Same IA is fine; visual language and density can differ |

---

## 4. Priority backlog (passes)

| ID | Pass | Acceptance (after maintainer confirm) |
| --- | --- | --- |
| **PU.0** | **Baseline audit** — screenshot current Runs / Flows / Deployments / Work pools / Run detail (+ DAG); note pain for “100s of flows”; refresh `ui_prefect_parity_checklist.md` checkmarks vs reality | Artifact pack + short written findings; checklist truthful |
| **PU.1** | **Visual foundation** — CSS variables, typography, spacing, state colors, table/list density, shell/nav; apply across AppShell without redesigning every page’s IA | Confirmed “direction” screenshots; no functional regressions |
| **PU.2** | **Runs ops triage** — state multi-filter, search, sort, denser table, “needs attention” default (failed/cancelled/running), cursor pagination UX that survives hundreds of rows | Usable with seeded ≥200 runs |
| **PU.3** | **Flows & deployments at scale** — catalog search, last-run state, schedule/pause visibility, quick-run affordance without hunting | Usable with many flows/deployments seeded |
| **PU.4** | **Run detail operator chrome** — action bar hierarchy (cancel / pause drain|terminate / resume / retry) once P3.2 APIs exist; logs/tasks/events scannable; interrupted vs completed clear (P1) | Matches lifecycle labels from lifecycle plan |
| **PU.5** | **Bulk / fleet actions (subset)** — multi-select cancel or pause on run list (only after single-run lifecycle is solid) | Explicit confirm dialogs; no silent bulk |
| **PU.6** | **Concurrency admin page** — can merge with canvas **P4.3** when ready; must match PU visual tokens | Same design system as PU.1 |
| **PU.7** | **Empty / loading / error / offline** polish + light keyboard shortcuts (focus search, j/k optional) | No blank confusing states |

**Suggested order:** `PU.0 → PU.1 → (confirm) → PU.2 → PU.3`, then interleave **PU.4** with P3.2e lifecycle UI, **PU.6** with P4.3.

---

## 5. Data for realistic review

- Extend or add a seed script (e.g. `scripts/ui_ops_seed.py`) that creates **many** flows/runs/deployments with mixed states — not only the happy-path demo seed.
- Optional: synthetic names (`etl-region-NNN`) so search/filter demos are obvious.
- Prefer local `data/` + API; no need for production volumes in v1 passes.

---

## 6. Explicit non-goals

- Pixel-perfect Prefect Cloud/OSS clone
- Full design-system package extract (keep CSS variables + existing React structure unless confirmed otherwise)
- Rewriting the DAG engine in PU.1
- Blocking P0/P1/P3 runtime work on a full visual freeze — **PU runs in parallel**, with confirm gates between passes

---

## 7. Handoff

When a pass is ready for confirm, PR description should include:

1. Pass ID  
2. Screenshot/recording links  
3. What to click through  
4. Open questions (max 3)  

Maintainer replies on the PR or chat with Approve / Tweak list / Reject direction.
