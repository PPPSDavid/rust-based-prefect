"""First-class flow catalog: identity, aliases, archive, delete, retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from ..errors import FlowCatalogConflict
from ..flow_catalog_settings import (
    catalog_hide_archived,
    orphan_flow_gc_enabled,
    run_retention_days,
)
from .types import PageResult

LIVE_FLOW_STATES = frozenset({"SCHEDULED", "PENDING", "RUNNING", "PAUSED"})
LIVE_DEPLOYMENT_RUN_STATUSES = frozenset({"SCHEDULED", "CLAIMED", "RUNNING"})


class FlowCatalogMixin:
    def _catalog_now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _flow_catalog_row(self, row: Any) -> dict[str, Any]:
        keys = row.keys() if hasattr(row, "keys") else []
        return {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "archived_at": row["archived_at"] if "archived_at" in keys else None,
            "deleted_at": row["deleted_at"] if "deleted_at" in keys else None,
        }

    def _lookup_flow_name(self, name: str) -> dict[str, Any] | None:
        current = self._get_flow_catalog_by_name(name)
        if current is not None:
            return current
        alias_rows = self._query_rows(
            "SELECT flow_id FROM flow_aliases WHERE name = ? LIMIT 1",
            [name],
        )
        if not alias_rows:
            return None
        return self._get_flow_catalog(str(alias_rows[0]["flow_id"]))

    def _get_flow_catalog_by_name(self, name: str) -> dict[str, Any] | None:
        rows = self._query_rows(
            "SELECT id,name,status,created_at,updated_at,archived_at,deleted_at "
            "FROM flows WHERE name = ? LIMIT 1",
            [name],
        )
        if not rows:
            return None
        return self._flow_catalog_row(rows[0])

    def _get_flow_catalog(self, flow_id: str) -> dict[str, Any] | None:
        rows = self._query_rows(
            "SELECT id,name,status,created_at,updated_at,archived_at,deleted_at "
            "FROM flows WHERE id = ? LIMIT 1",
            [flow_id],
        )
        if not rows:
            return None
        return self._flow_catalog_row(rows[0])

    def _aliases_for(self, flow_id: str) -> list[str]:
        rows = self._query_rows(
            "SELECT name FROM flow_aliases WHERE flow_id = ? ORDER BY name",
            [flow_id],
        )
        return [str(row["name"]) for row in rows]

    def _undeleted_deployments(self, flow_id: str) -> list[dict[str, Any]]:
        rows = self._query_rows(
            """
            SELECT id,name,flow_name,paused,schedule_enabled
            FROM deployments
            WHERE flow_id = ? AND deleted_at IS NULL
            ORDER BY name
            """,
            [flow_id],
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "flow_name": row["flow_name"],
                "paused": bool(row["paused"]),
                "schedule_enabled": bool(row["schedule_enabled"]),
            }
            for row in rows
        ]

    def _require_no_undeleted_deployments(self, flow_id: str, action: str) -> None:
        blocking = self._undeleted_deployments(flow_id)
        if blocking:
            names = ", ".join(item["name"] for item in blocking)
            raise FlowCatalogConflict(
                f"cannot {action}: undeleted deployments remain ({names})",
                code="undeleted_deployments",
                deployments=blocking,
            )

    def _name_taken(self, name: str, *, ignore_flow_id: str | None = None) -> bool:
        rows = self._query_rows("SELECT id FROM flows WHERE name = ? LIMIT 1", [name])
        if rows and str(rows[0]["id"]) != ignore_flow_id:
            return True
        alias = self._query_rows(
            "SELECT flow_id FROM flow_aliases WHERE name = ? LIMIT 1", [name]
        )
        return bool(alias) and str(alias[0]["flow_id"]) != ignore_flow_id

    def _insert_alias(self, name: str, flow_id: str) -> None:
        existing = self._query_rows(
            "SELECT flow_id FROM flow_aliases WHERE name = ? LIMIT 1", [name]
        )
        if existing:
            if str(existing[0]["flow_id"]) != flow_id:
                raise FlowCatalogConflict(
                    f"name {name!r} is already an alias of another flow",
                    code="alias_reserved",
                )
            return
        current = self._query_rows(
            "SELECT id FROM flows WHERE name = ? LIMIT 1", [name]
        )
        if current and str(current[0]["id"]) != flow_id:
            raise FlowCatalogConflict(
                f"name {name!r} is already the canonical name of another flow",
                code="name_conflict",
            )
        if current and str(current[0]["id"]) == flow_id:
            return
        self._sqlite_conn.execute(
            "INSERT INTO flow_aliases(name,flow_id,created_at) VALUES(?,?,?)",
            [name, flow_id, self._catalog_now()],
        )

    def _ensure_flow_canonical_rust(self, name: str) -> dict[str, Any] | None:
        rust = self._rust_deployment_dispatch("ensure_flow_canonical", {"name": name})
        if rust is None:
            return None
        if rust.get("ok") and isinstance(rust.get("flow"), dict):
            return rust["flow"]
        err = rust.get("error") or {}
        code = err.get("code")
        if code in {"alias_reserved", "deleted_flow"}:
            raise FlowCatalogConflict(str(err.get("message") or code), code=str(code))
        return None

    def _create_flow_row(self, name: str) -> dict[str, Any]:
        now = self._catalog_now()
        flow_id = str(uuid4())
        self._sqlite_conn.execute(
            "INSERT INTO flows(id,name,status,created_at,updated_at) VALUES(?,?,?,?,?)",
            [flow_id, name, "active", now, now],
        )
        row = self._get_flow_catalog(flow_id)
        assert row is not None
        return row

    def ensure_flow(
        self, name: str, *, formerly: list[str] | tuple[str, ...] | None = None
    ) -> dict[str, Any]:
        """Upsert catalog identity. Rename/merge only when source has zero undeleted deployments."""
        if not name or not str(name).strip():
            raise ValueError("flow name is required")
        canonical = str(name).strip()
        former_names = [
            item.strip()
            for item in (formerly or ())
            if item and str(item).strip() and str(item).strip() != canonical
        ]
        with self._lock:
            if not former_names:
                rust_flow = self._ensure_flow_canonical_rust(canonical)
                if rust_flow is not None:
                    return rust_flow
            target = self._get_flow_catalog_by_name(canonical)
            sources = [
                self._lookup_flow_name(old)
                for old in former_names
                if self._lookup_flow_name(old) is not None
            ]
            if target is None and not sources:
                if self._name_taken(canonical) or any(
                    self._name_taken(old) for old in former_names
                ):
                    raise FlowCatalogConflict(
                        f"name {canonical!r} is reserved as an alias of another flow"
                        if self._name_taken(canonical)
                        else "a formerly name is reserved as an alias of another flow",
                        code="alias_reserved",
                    )
                created = self._create_flow_row(canonical)
                for old in former_names:
                    self._insert_alias(old, created["id"])
                return created
            if target is None and sources:
                primary = sources[0]
                self._require_no_undeleted_deployments(primary["id"], "rename")
                self._rename_flow_unlocked(primary["id"], canonical)
                target = self._get_flow_catalog(primary["id"])
                assert target is not None
            assert target is not None
            for source in sources:
                if source["id"] == target["id"]:
                    continue
                if source["status"] == "deleted":
                    continue
                self._require_no_undeleted_deployments(source["id"], "merge")
                self._merge_flows_unlocked(source["id"], target["id"])
            for old in former_names:
                self._insert_alias(old, target["id"])
            restored = self._get_flow_catalog(target["id"])
            assert restored is not None
            if restored["status"] == "deleted":
                raise FlowCatalogConflict(
                    f"flow {canonical!r} is deleted; restore it before reuse",
                    code="deleted_flow",
                )
            return restored

    def _rename_flow_unlocked(self, flow_id: str, new_name: str) -> None:
        current = self._get_flow_catalog(flow_id)
        if current is None:
            raise ValueError("flow not found")
        old_name = current["name"]
        if old_name == new_name:
            return
        if self._name_taken(new_name, ignore_flow_id=flow_id):
            raise FlowCatalogConflict(
                f"cannot rename to {new_name!r}: name is reserved",
                code="name_conflict",
            )
        now = self._catalog_now()
        self._sqlite_conn.execute(
            "DELETE FROM flow_aliases WHERE name = ? AND flow_id = ?",
            [new_name, flow_id],
        )
        self._sqlite_conn.execute(
            "UPDATE flows SET name = ?, updated_at = ?, status = CASE "
            "WHEN status = 'archived' THEN 'active' ELSE status END, "
            "archived_at = CASE WHEN status = 'archived' THEN NULL ELSE archived_at END "
            "WHERE id = ?",
            [new_name, now, flow_id],
        )
        self._insert_alias(old_name, flow_id)

    def _merge_flows_unlocked(self, source_id: str, target_id: str) -> None:
        source = self._get_flow_catalog(source_id)
        if source is None:
            return
        now = self._catalog_now()
        self._sqlite_conn.execute(
            "UPDATE flow_runs SET flow_id = ? WHERE flow_id = ?",
            [target_id, source_id],
        )
        self._sqlite_conn.execute(
            "UPDATE deployments SET flow_id = ? WHERE flow_id = ?",
            [target_id, source_id],
        )
        for alias in self._aliases_for(source_id) + [source["name"]]:
            self._sqlite_conn.execute(
                "DELETE FROM flow_aliases WHERE name = ?", [alias]
            )
            if alias != self._get_flow_catalog(target_id)["name"]:
                self._insert_alias(alias, target_id)
        self._sqlite_conn.execute("DELETE FROM flows WHERE id = ?", [source_id])
        self._sqlite_conn.execute(
            "UPDATE flows SET updated_at = ? WHERE id = ?", [now, target_id]
        )

    def attach_run_to_flow(self, run_id: UUID, flow_id: str) -> None:
        self._sqlite_conn.execute(
            "UPDATE flow_runs SET flow_id = ? WHERE id = ?",
            [flow_id, str(run_id)],
        )

    def list_flows(
        self,
        limit: int = 200,
        cursor: str | None = None,
        status: str | None = None,
    ) -> PageResult:
        hide = catalog_hide_archived()
        rust_result = self._query_rust(
            "flows",
            {
                "limit": limit,
                "cursor": cursor,
                "status": status,
                "hide_archived": hide if status is None else False,
            },
        )
        if rust_result is not None and rust_result.get("catalog"):
            return PageResult(
                items=rust_result["items"], next_cursor=rust_result["next_cursor"]
            )
        wanted = status
        if wanted is None:
            wanted = "active" if hide else None
        query = (
            "SELECT f.id,f.name,f.status,f.created_at,f.updated_at,f.archived_at,f.deleted_at,"
            "(SELECT COUNT(*) FROM flow_runs fr WHERE fr.flow_id = f.id) AS run_count "
            "FROM flows f"
        )
        params: list[Any] = []
        conditions: list[str] = []
        if wanted:
            conditions.append("f.status = ?")
            params.append(wanted)
        elif hide:
            conditions.append("f.status = 'active'")
        if cursor:
            conditions.append("f.updated_at < ?")
            params.append(cursor)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY f.updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = []
        for row in rows:
            item = self._flow_catalog_row(row)
            item["run_count"] = int(row["run_count"] or 0)
            items.append(item)
        next_cursor = str(rows[-1]["updated_at"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def get_flow_catalog_detail(self, name_or_id: str) -> dict[str, Any] | None:
        catalog = self._get_flow_catalog(name_or_id)
        resolved_from_alias = False
        requested = name_or_id
        if catalog is None:
            catalog = self._lookup_flow_name(name_or_id)
            if catalog is not None and catalog["name"] != name_or_id:
                resolved_from_alias = True
        if catalog is None:
            return None
        if catalog["status"] == "deleted":
            return None
        tasks = self.list_tasks(flow_name=catalog["name"], limit=500)
        dep_rows = self._query_rows(
            """
            SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                   concurrency_limit,collision_strategy,schedule_interval_seconds,
                   schedule_cron,schedule_rrule,schedule_next_run_at,schedule_enabled,
                   work_pool_id,flow_id,deleted_at,created_at,updated_at
            FROM deployments
            WHERE deleted_at IS NULL AND (flow_id = ? OR flow_name = ?)
            ORDER BY name LIMIT 500
            """,
            [catalog["id"], catalog["name"]],
        )
        deployments = [self._deployment_row_to_dict(row) for row in dep_rows]
        runs = self._query_rows(
            "SELECT seq,id,name,state,version,created_at,updated_at "
            "FROM flow_runs WHERE flow_id = ? ORDER BY seq DESC LIMIT 20",
            [catalog["id"]],
        )
        return {
            **catalog,
            "aliases": self._aliases_for(catalog["id"]),
            "canonical_name": catalog["name"],
            "resolved_from_alias": resolved_from_alias,
            "requested_name": requested,
            "tasks": tasks,
            "deployments": deployments,
            "recent_runs": [self._flow_row_to_dict(row) for row in runs],
        }

    def rename_flow(self, flow_id: str, new_name: str) -> dict[str, Any]:
        with self._lock:
            catalog = self._get_flow_catalog(flow_id)
            if catalog is None:
                raise ValueError("flow not found")
            if catalog["status"] == "deleted":
                raise FlowCatalogConflict(
                    "cannot rename a deleted flow", code="deleted_flow"
                )
            self._require_no_undeleted_deployments(flow_id, "rename")
            self._rename_flow_unlocked(flow_id, str(new_name).strip())
            result = self._get_flow_catalog(flow_id)
            assert result is not None
            result["aliases"] = self._aliases_for(flow_id)
            return result

    def archive_flow(self, flow_id: str) -> dict[str, Any]:
        with self._lock:
            catalog = self._get_flow_catalog(flow_id)
            if catalog is None:
                raise ValueError("flow not found")
            if catalog["status"] == "deleted":
                raise FlowCatalogConflict(
                    "cannot archive a deleted flow", code="deleted_flow"
                )
            self._require_no_undeleted_deployments(flow_id, "archive")
            now = self._catalog_now()
            self._sqlite_conn.execute(
                "UPDATE flows SET status = 'archived', archived_at = ?, updated_at = ? WHERE id = ?",
                [now, now, flow_id],
            )
            result = self._get_flow_catalog(flow_id)
            assert result is not None
            return result

    def restore_flow(self, flow_id: str) -> dict[str, Any]:
        with self._lock:
            catalog = self._get_flow_catalog(flow_id)
            if catalog is None:
                raise ValueError("flow not found")
            now = self._catalog_now()
            self._sqlite_conn.execute(
                "UPDATE flows SET status = 'active', archived_at = NULL, deleted_at = NULL, "
                "updated_at = ? WHERE id = ?",
                [now, flow_id],
            )
            result = self._get_flow_catalog(flow_id)
            assert result is not None
            return result

    def delete_flow(self, flow_id: str) -> dict[str, Any]:
        with self._lock:
            catalog = self._get_flow_catalog(flow_id)
            if catalog is None:
                raise ValueError("flow not found")
            self._require_no_undeleted_deployments(flow_id, "delete")
            live = self._query_rows(
                "SELECT id FROM flow_runs WHERE flow_id = ? AND state IN "
                "('SCHEDULED','PENDING','RUNNING','PAUSED') LIMIT 1",
                [flow_id],
            )
            if live:
                raise FlowCatalogConflict(
                    "cannot delete a flow with live runs",
                    code="live_runs",
                )
            now = self._catalog_now()
            self._sqlite_conn.execute(
                "UPDATE flows SET status = 'deleted', deleted_at = ?, updated_at = ? WHERE id = ?",
                [now, now, flow_id],
            )
            result = self._get_flow_catalog(flow_id)
            assert result is not None
            return result

    def _deployment_has_live_work(self, deployment_id: str) -> list[str]:
        reasons: list[str] = []
        dep_runs = self._query_rows(
            """
            SELECT id FROM deployment_runs
            WHERE deployment_id = ? AND status IN ('SCHEDULED','CLAIMED','RUNNING')
            LIMIT 1
            """,
            [deployment_id],
        )
        if dep_runs:
            reasons.append("live deployment runs")
        flow_runs = self._query_rows(
            """
            SELECT fr.id FROM flow_runs fr
            JOIN deployment_runs dr ON dr.flow_run_id = fr.id
            WHERE dr.deployment_id = ? AND fr.state IN
            ('SCHEDULED','PENDING','RUNNING','PAUSED')
            LIMIT 1
            """,
            [deployment_id],
        )
        if flow_runs:
            reasons.append("live flow runs")
        return reasons

    def delete_deployment(self, deployment_id: UUID) -> dict[str, Any]:
        dep_id = str(deployment_id)
        with self._lock:
            rows = self._query_rows(
                "SELECT id,name,flow_id,schedule_enabled,deleted_at FROM deployments WHERE id = ? LIMIT 1",
                [dep_id],
            )
            if not rows or rows[0]["deleted_at"]:
                raise ValueError("deployment not found")
            row = rows[0]
            if int(row["schedule_enabled"] or 0):
                raise FlowCatalogConflict(
                    "cannot delete a deployment while its schedule is enabled; disable the schedule first",
                    code="schedule_enabled",
                    deployments=[{"id": dep_id, "name": row["name"]}],
                )
            live = self._deployment_has_live_work(dep_id)
            if live:
                raise FlowCatalogConflict(
                    "cannot delete a deployment with " + " and ".join(live),
                    code="live_runs",
                    deployments=[{"id": dep_id, "name": row["name"]}],
                )
            now = self._catalog_now()
            tombstone = f"{row['name']}__deleted__{dep_id[:8]}"
            self._sqlite_conn.execute(
                "UPDATE deployments SET deleted_at = ?, name = ?, updated_at = ? WHERE id = ?",
                [now, tombstone, now, dep_id],
            )
            return {"deleted": True, "id": dep_id, "name": row["name"]}

    def apply_deployments(
        self, items: list[dict[str, Any]], *, prune: bool = False
    ) -> dict[str, Any]:
        keep_names = {str(item["name"]) for item in items if item.get("name")}
        keep_flow_names = {
            str(item["flow_name"]) for item in items if item.get("flow_name")
        }
        renamed: list[dict[str, Any]] = []
        created_or_updated: list[dict[str, Any]] = []
        pruned: list[dict[str, Any]] = []
        archived: list[dict[str, Any]] = []
        with self._lock:
            for item in items:
                flow_name = str(item.get("flow_name") or "")
                formerly = item.get("formerly") or []
                if not isinstance(formerly, list):
                    formerly = [formerly]
                former_list = [str(x) for x in formerly if x]
                if former_list:
                    for old in former_list:
                        source = self._lookup_flow_name(old)
                        if source is None:
                            continue
                        for dep in self._undeleted_deployments(source["id"]):
                            if dep["name"] not in keep_names:
                                pruned.append(self.delete_deployment(UUID(dep["id"])))
                    catalog = self.ensure_flow(flow_name, formerly=former_list)
                    renamed.append(catalog)
                else:
                    self.ensure_flow(flow_name)
            for item in items:
                created_or_updated.append(self._upsert_deployment_from_apply(item))
            if prune:
                alive = self._query_rows(
                    "SELECT id,name,flow_id FROM deployments WHERE deleted_at IS NULL",
                    [],
                )
                for row in alive:
                    if row["name"] not in keep_names:
                        pruned.append(self.delete_deployment(UUID(str(row["id"]))))
                archived.extend(self._archive_orphan_flows_unlocked(keep_flow_names))
        return {
            "renamed": renamed,
            "deployments": created_or_updated,
            "pruned": pruned,
            "archived": archived,
        }

    def _upsert_deployment_from_apply(self, item: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_deployment_by_name(str(item["name"]))
        if existing is not None:
            patch = {
                key: value
                for key, value in item.items()
                if key not in {"name", "flow_name", "formerly"}
            }
            return self.update_deployment(UUID(str(existing["id"])), patch)
        return self.create_deployment(
            name=str(item["name"]),
            flow_name=str(item["flow_name"]),
            entrypoint=item.get("entrypoint"),
            path=item.get("path"),
            default_parameters=item.get("default_parameters") or {},
            paused=bool(item.get("paused", False)),
            concurrency_limit=item.get("concurrency_limit"),
            collision_strategy=str(item.get("collision_strategy") or "ENQUEUE"),
            schedule_interval_seconds=item.get("schedule_interval_seconds"),
            schedule_cron=item.get("schedule_cron"),
            schedule_rrule=item.get("schedule_rrule"),
            schedule_next_run_at=item.get("schedule_next_run_at"),
            schedule_enabled=bool(item.get("schedule_enabled", False)),
            work_pool_id=item.get("work_pool_id"),
            formerly=item.get("formerly") or [],
        )

    def _archive_orphan_flows_unlocked(
        self, keep_flow_names: set[str]
    ) -> list[dict[str, Any]]:
        now = self._catalog_now()
        keep = list(keep_flow_names)
        if keep:
            placeholders = ",".join("?" * len(keep))
            keep_sql = f"AND name NOT IN ({placeholders})"
            select_params: list[Any] = keep
        else:
            keep_sql = ""
            select_params = []
        rows = self._query_rows(
            f"""
            SELECT id FROM flows
            WHERE status = 'active' {keep_sql}
            AND NOT EXISTS (
                SELECT 1 FROM deployments d
                WHERE d.flow_id = flows.id AND d.deleted_at IS NULL
            )
            """,
            select_params,
        )
        ids = [str(row["id"]) for row in rows]
        if not ids:
            return []
        id_placeholders = ",".join("?" * len(ids))
        self._sqlite_conn.execute(
            f"UPDATE flows SET status = 'archived', archived_at = ?, updated_at = ? "
            f"WHERE id IN ({id_placeholders})",
            [now, now, *ids],
        )
        archived: list[dict[str, Any]] = []
        for flow_id in ids:
            item = self._get_flow_catalog(flow_id)
            if item is not None:
                archived.append(item)
        return archived

    def retention_sweep(self) -> dict[str, int]:
        days = run_retention_days()
        cutoff = (
            (datetime.now(UTC) - timedelta(days=days)).isoformat() if days > 0 else None
        )
        rust = self._rust_deployment_dispatch(
            "catalog_retention_sweep",
            {"cutoff": cutoff, "gc_orphans": orphan_flow_gc_enabled()},
        )
        if rust is not None and rust.get("ok"):
            return {
                "deleted_runs": int(rust.get("deleted_runs") or 0),
                "gc_flows": int(rust.get("gc_flows") or 0),
            }
        return self._retention_sweep_sql(cutoff)

    def _retention_sweep_sql(self, cutoff: str | None) -> dict[str, int]:
        deleted_runs = 0
        gc_flows = 0
        live = "('SCHEDULED','PENDING','RUNNING','PAUSED')"
        with self._lock:
            if cutoff:
                expired = (
                    "SELECT id FROM flow_runs WHERE updated_at < ? "
                    f"AND state NOT IN {live}"
                )
                for table, col in (
                    ("task_runs", "flow_run_id"),
                    ("logs", "flow_run_id"),
                    ("events", "run_id"),
                    ("artifacts", "flow_run_id"),
                    ("dag_manifests", "flow_run_id"),
                ):
                    self._sqlite_conn.execute(
                        f"DELETE FROM {table} WHERE {col} IN ({expired})",
                        [cutoff],
                    )
                cur = self._sqlite_conn.execute(
                    f"DELETE FROM flow_runs WHERE updated_at < ? AND state NOT IN {live}",
                    [cutoff],
                )
                deleted_runs = int(cur.rowcount or 0)
            if orphan_flow_gc_enabled():
                orphan = (
                    "status IN ('archived','deleted') "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM deployments d WHERE d.flow_id = flows.id "
                    "AND d.deleted_at IS NULL) "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM flow_runs fr WHERE fr.flow_id = flows.id)"
                )
                self._sqlite_conn.execute(
                    f"DELETE FROM flow_aliases WHERE flow_id IN (SELECT id FROM flows WHERE {orphan})"
                )
                cur = self._sqlite_conn.execute(f"DELETE FROM flows WHERE {orphan}")
                gc_flows = int(cur.rowcount or 0)
        return {"deleted_runs": deleted_runs, "gc_flows": gc_flows}
