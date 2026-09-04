# How to rename, archive, and delete flows

IronFlow keeps a **UUID-stable flow catalog**. Display names are mutable. This is an **IronFlow extension**, not Prefect API parity — Prefect OSS cannot rename a flow in place ([prefect#8154](https://github.com/PrefectHQ/prefect/issues/8154)).

## Source of truth

**If you still have the `@flow` definition, that file is the source of truth.** Rename by changing the decorator and deploying. Do not click Rename in the UI and leave the Python name unchanged — the next `serve()` / run of the old name will conflict (`alias_reserved`) instead of silently tracking the catalog.

| You have… | Rename with… |
| --- | --- |
| Flow source (`.py` / `ironflow.yaml`) | **Primary:** `@flow(name="B", formerly=["A"])` plus `ironflow deploy --all --prune` |
| No undeleted deployment, and no source you will keep running | **Catalog-only:** UI Rename / `ironflow flow rename` |
| A live or paused deployment still on the old identity | Remove or prune that deployment first (or use the primary deploy path, which prunes) |

`serve()` / `ironflow serve` upserts **one** deployment and starts a worker. It is **not** a rename. Re-serving with a new `@flow(name=...)` and no `formerly=` creates a **second** catalog identity.

## Bar (do not interchange these)

| Action | What it does | History |
| --- | --- | --- |
| **Rename** | Same catalog UUID, new canonical name. The old name becomes a reserved **alias** (GitHub-style). | Follows the UUID. `flow_runs.name` / `deployments.flow_name` stay execution-time snapshots. |
| **Archive** | Hide from default lists; keep the identity. Visible in the **Archived** panel; restorable. | Runs stay until TTL. Use when there is **no successor**. |
| **Delete (soft)** | Hide from default lists **and** Archived. The name stays reserved until orphan GC. | Runs linger until `IRONFLOW_RUN_RETENTION_DAYS`. |

**Undeleted deployment fence:** rename, archive, and flow-delete return **HTTP 409** while any deployment row exists that has not been soft-deleted — **including paused**. Pause is reversible. There is no `cascade_pause`.

**Deployment delete fences:** refuse while live runs exist (`SCHEDULED|PENDING|RUNNING|PAUSED` on the flow run, or `SCHEDULED|CLAIMED|RUNNING` on the deployment run) or `schedule_enabled=true`. Soft-delete suffixes the unique name `{name}__deleted__{id[:8]}`.

These are accident interlocks, not auth. Access control remains `IRONFLOW_SERVER_API_AUTH_STRING`.

## Primary — rename A → B in source

Declare the successor name and the old name. Then prune-and-replace deploy so the old deployment is gone **before** the catalog rename commits.

```python
from prefect_compat import flow

@flow(name="B", formerly=["A"])
def pipeline():
    return "ok"
```

```yaml
ironflow-version: "1"
deployments:
  - name: B-prod
    flow_name: B
    formerly: ["A"]
    entrypoint: flows/pipeline.py:pipeline
```

```bash
ironflow deploy --file ironflow.yaml --all --prune
```

`--prune` requires `--all`. The apply transaction:

1. Soft-deletes deployments not in the new manifest (here `A-prod`).
2. Renames catalog UUID in place (`A` → `B`); `A` becomes an alias.
3. Creates `B-prod`.

`GET /api/flows/A` resolves to canonical `B` with `resolved_from_alias=true`. The UI shows an alias banner on that old URL.

If `A-prod` still exists (you deployed without `--prune`, or only ran `serve()`), rename is **409**.

Calling the decorated function in-process (`pipeline()`) also passes `formerly=` into `ensure_flow`. That rename still requires **zero undeleted deployments** on `A`. Use it for flows you only ever ran locally — not as a substitute for prune-and-replace when a served deployment exists.

## Catalog-only — UI or CLI when there is no source deploy

The Flows UI (**Rename**, **Archive**, **Delete**) and `ironflow flow rename|archive|delete` talk to the catalog only. They do **not** rewrite your `.py` file.

Use them when **all** of the following are true:

- There is **no undeleted deployment** (you never called `serve()` / `ironflow deploy` for this identity, or you already deleted/pruned it).
- You want to change or hide the catalog row (display name, Archived, soft-delete).
- You will **not** keep executing a source file that still claims the old `@flow(name=...)`.

Typical cases: an in-process run that created a catalog row, a leftover identity after the code was already removed, or a display-name fix when you are not going to redeploy from source.

If the flow code is still in the repo and you will run or serve it, **edit the decorator** (`formerly=`) and use the primary deploy path instead. A UI rename to `B` while the file still says `@flow(name="A")` does not auto-heal: the next `ensure_flow("A")` hits the reserved alias and fails. Point the file at `B`, or rename back with `@flow(name="A", formerly=["B"])`.

The UI also **reads** aliases: visiting `/flows/A` after a source or catalog rename shows that `A` is now an alias of `B`. Active vs **Archived** lists hide retired identities; that is inspection, not a rename workflow.

## Remove the Python / YAML and retire the identity

Deleting the file does **nothing** in the catalog. Identities are not inferred from the filesystem.

```bash
ironflow deploy --file ironflow.yaml --all --prune
```

When the last undeleted deployment for that flow is gone, apply **auto-archives** the flow (no successor). It disappears from the default Flows list and shows up under **Archived**. Restore from the UI, `ironflow flow restore <id>`, or `POST /api/flows/{id}/restore`.

Use UI/CLI archive or delete for the same leftover only after deployments are gone and you are not replacing it with a successor name in source.

## Destroy a live or paused flow (blocked)

Archive, delete, and rename of a flow that still has an undeleted deployment (paused included) is **409**. Delete of a deployment that still has live runs or `schedule_enabled=true` is **409**. Disable the schedule, let runs finish (or cancel them), then delete the deployment, then archive or delete the flow — or prune via the primary deploy path.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `IRONFLOW_CATALOG_HIDE_ARCHIVED` | `1` | Default lists hide archived/deleted catalog rows and their runs |
| `IRONFLOW_RUN_RETENTION_DAYS` | `90` | Terminal run TTL; `0` disables |
| `IRONFLOW_ORPHAN_FLOW_GC` | `1` | Drop archived/deleted catalog rows with no remaining runs and no undeleted deployments |

`GET /api/server-info` echoes these flags.

Hot paths (persist attach, catalog list/join, set-based TTL sweep) run in **Rust** when `bind_db` is active; Python is the fallback.

## CLI

Catalog-only operators (same fences as the UI). Prefer `ironflow deploy --all --prune` when source still exists.

```bash
ironflow flow ls
ironflow flow ls --status archived
ironflow flow inspect B
ironflow flow rename <flow-uuid> C
ironflow flow archive <flow-uuid>
ironflow flow restore <flow-uuid>
ironflow flow delete <flow-uuid>
```

## HTTP

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/server-info` | Catalog env flags |
| `GET` | `/api/flows` | Catalog list (`status=active\|archived`) |
| `GET` | `/api/flows/{name}` | Detail; aliases resolve |
| `POST` | `/api/flows/{id}/rename` | Catalog-only in-place rename (not a source edit) |
| `POST` | `/api/flows/{id}/archive` | Archive |
| `POST` | `/api/flows/{id}/restore` | Restore |
| `DELETE` | `/api/flows/{id}` | Soft-delete |
| `DELETE` | `/api/deployments/{id}` | Soft-delete deployment |
| `POST` | `/api/deployments/apply` | **Primary rename:** prune + `formerly=` + upsert (`prune=true`) |
| `GET` | `/api/flow-runs` | Default hides archived catalog runs (`include_archived=true` to show them) |

Conflict payloads: `{ "code": "...", "message": "...", "deployments": [...] }` with HTTP 409.

## Related

- [How to deploy with the CLI](deploy-with-cli.md) — `--all --prune`
- [Compatibility matrix](../compatibility.md) — IronFlow catalog vs Prefect name identity
- [Flows](../concepts/flows.md) — catalog identity vs `@flow(name=...)`
- Plan: `docs/plans/flow-catalog-lifecycle.md`
