# Flow catalog lifecycle

**Status:** Implemented (Phase 1 catalog + Phase 2 retention)  
**Kind:** IronFlow extension (not Prefect API parity)  
**Related:** `docs/how-to/rename-archive-flows.md`

## Problem

IronFlow had no flow entity. The UI catalog was `GROUP BY name` on `flow_runs`. Renaming `@flow` from `A` to `B` created a second identity; both stayed searchable; old URLs never cleaned up. Prefect OSS has the same trap (`PATCH /flows/{id}` cannot change `name`; [issue #8154](https://github.com/PrefectHQ/prefect/issues/8154)).

## Bar (do not interchange)

- **Rename** — same catalog UUID, new canonical name. Old name becomes a reserved **alias**. History follows the UUID. Not a second catalog row.
- **Archive** — stop showing it; keep it. Hidden from default lists; visible in the Archived panel; restorable. Use when the identity is retired **with no successor**.
- **Delete (soft)** — stop keeping it as a catalog object. Hidden from default lists and the Archived panel. Name reserved until orphan GC. Runs remain until TTL.

**Undeleted deployment fence:** rename, archive, and flow-delete refuse while any deployment row exists that has not been soft-deleted — **including paused**. Pause is reversible. No `cascade_pause`.

**Deployment delete fences:** refuse while live runs exist (`SCHEDULED|PENDING|RUNNING|PAUSED` on the flow run, or `SCHEDULED|CLAIMED|RUNNING` on the deployment run) or `schedule_enabled=true`.

These are accident interlocks, not auth. Access control remains `IRONFLOW_SERVER_API_AUTH_STRING`.

## Model

- `flows(id, name, status, timestamps)` with `status` in `active|archived|deleted`
- `flow_aliases(name PK → flow_id)` — former names, globally reserved
- `flow_runs.flow_id`, `deployments.flow_id` — `name` / `flow_name` stay execution-time snapshots
- `deployments.deleted_at` — soft-delete (name is suffixed to free UNIQUE)

`@flow(name="B", formerly=["A"])` records intent. Rename commits only when the source has **zero undeleted deployments**. The supported one-shot is `ironflow deploy --all --prune` (scoped prune of replaced deployments, then rename, then create).

## Env

| Variable | Default | Meaning |
| --- | --- | --- |
| `IRONFLOW_CATALOG_HIDE_ARCHIVED` | `1` | Default lists hide archived/deleted |
| `IRONFLOW_RUN_RETENTION_DAYS` | `90` | Terminal run TTL; `0` disables |
| `IRONFLOW_ORPHAN_FLOW_GC` | `1` | Drop archived/deleted catalog rows with no runs and no undeleted deployments |

Exposed on `GET /api/server-info`.

## Worked cases

See **[How to rename, archive, and delete flows](../how-to/rename-archive-flows.md)**. Maintainer summary:

1. **Primary rename A→B in source** — `formerly=` + `ironflow deploy --all --prune` deletes `A-prod`, renames UUID in place, creates `B-prod`. `/flows/A` resolves to `B`. The `.py` file is source of truth when it still exists.
2. **Catalog-only UI/CLI** — rename/archive/delete when there is no undeleted deployment and you will not keep running the old decorator name. Does not edit source.
3. **Remove B’s Python/yaml** — file deletion alone does nothing. `deploy --all --prune` deletes the last deployment and auto-archives `B`.
4. **Destroy a live flow** — archive/delete/rename `409` while any undeleted deployment exists (paused included). Deployment delete `409` while live runs or schedule-on.

## Conflict rules

- `formerly=["A"]` while `A` is an alias of a **different** live flow → 400
- New `@flow(name="A")` when `A` is a reserved alias → 400
- Rename/merge/archive/flow-delete with undeleted deployments → 409
- Soft-deleted flow restorable until orphan GC; then the name may be reused
