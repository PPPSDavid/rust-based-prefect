# How to rename, archive, and delete flows

IronFlow keeps a **UUID-stable flow catalog**. Display names are mutable. This is an **IronFlow extension**, not Prefect API parity — Prefect OSS cannot rename a flow in place ([prefect#8154](https://github.com/PrefectHQ/prefect/issues/8154)).

**Bar (do not interchange these):**

| Action | What it does | History |
| --- | --- | --- |
| **Rename** | Same catalog UUID, new canonical name. The old name becomes a reserved **alias** (GitHub-style). | Follows the UUID. `flow_runs.name` / `deployments.flow_name` stay execution-time snapshots. |
| **Archive** | Hide from default lists; keep the identity. Visible in the **Archived** panel; restorable. | Runs stay until TTL. Use when there is **no successor**. |
| **Delete (soft)** | Hide from default lists **and** Archived. The name stays reserved until orphan GC. | Runs linger until `IRONFLOW_RUN_RETENTION_DAYS`. |

**Undeleted deployment fence:** rename, archive, and flow-delete return **HTTP 409** while any deployment row exists that has not been soft-deleted — **including paused**. Pause is reversible. There is no `cascade_pause`.

**Deployment delete fences:** refuse while live runs exist (`SCHEDULED|PENDING|RUNNING|PAUSED` on the flow run, or `SCHEDULED|CLAIMED|RUNNING` on the deployment run) or `schedule_enabled=true`. Soft-delete suffixes the unique name `{name}__deleted__{id[:8]}`.

These are accident interlocks, not auth. Access control remains `IRONFLOW_SERVER_API_AUTH_STRING`.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `IRONFLOW_CATALOG_HIDE_ARCHIVED` | `1` | Default lists hide archived/deleted catalog rows and their runs |
| `IRONFLOW_RUN_RETENTION_DAYS` | `90` | Terminal run TTL; `0` disables |
| `IRONFLOW_ORPHAN_FLOW_GC` | `1` | Drop archived/deleted catalog rows with no remaining runs and no undeleted deployments |

`GET /api/server-info` echoes these flags.

Hot paths (persist attach, catalog list/join, set-based TTL sweep) run in **Rust** when `bind_db` is active; Python is the fallback.

## Case 1 — Rename A → B in one PR

Intent: `@flow(name="B", formerly=["A"])`. The one-shot operator path is prune-and-replace deploy.

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

`GET /api/flows/A` resolves to canonical `B` with `resolved_from_alias=true`. The UI shows an alias banner.

If `A-prod` still exists (you deployed without `--prune`), rename is **409**.

## Case 2 — Remove the Python / YAML and retire the identity

Deleting the file does **nothing** in the catalog. Identities are not inferred from the filesystem.

```bash
ironflow deploy --file ironflow.yaml --all --prune
```

When the last undeleted deployment for that flow is gone, apply **auto-archives** the flow (no successor). It disappears from the default Flows list and shows up under **Archived**. Restore from the UI, `ironflow flow restore <id>`, or `POST /api/flows/{id}/restore`.

## Case 3 — Destroy a live or paused flow (blocked)

Archive, delete, and rename of a flow that still has an undeleted deployment (paused included) is **409**. Delete of a deployment that still has live runs or `schedule_enabled=true` is **409**. Disable the schedule, let runs finish (or cancel them), then delete the deployment, then archive or delete the flow.

## CLI

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
| `POST` | `/api/flows/{id}/rename` | In-place rename |
| `POST` | `/api/flows/{id}/archive` | Archive |
| `POST` | `/api/flows/{id}/restore` | Restore |
| `DELETE` | `/api/flows/{id}` | Soft-delete |
| `DELETE` | `/api/deployments/{id}` | Soft-delete deployment |
| `POST` | `/api/deployments/apply` | Prune + rename + upsert (`prune=true`) |
| `GET` | `/api/flow-runs` | Default hides archived catalog runs (`include_archived=true` to show them) |

Conflict payloads: `{ "code": "...", "message": "...", "deployments": [...] }` with HTTP 409.

## Related

- [How to deploy with the CLI](deploy-with-cli.md) — `--all --prune`
- [Compatibility matrix](../compatibility.md) — IronFlow catalog vs Prefect name identity
- Plan: `docs/plans/flow-catalog-lifecycle.md`
