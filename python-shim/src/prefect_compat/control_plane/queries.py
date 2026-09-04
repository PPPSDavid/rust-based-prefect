from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from ..flow_catalog_settings import catalog_hide_archived
from .types import (
    PageResult,
)


class QueriesMixin:
    def list_flow_runs(
        self,
        state: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        include_archived: bool = False,
    ) -> PageResult:
        hide = catalog_hide_archived() and not include_archived
        rust_result = self._query_rust(
            "flow_runs",
            {
                "state": state,
                "limit": limit,
                "cursor": cursor,
                "hide_archived": hide,
            },
        )
        if rust_result is not None:
            return PageResult(
                items=rust_result["items"], next_cursor=rust_result["next_cursor"]
            )
        query = (
            "SELECT fr.seq,fr.id,fr.name,fr.state,fr.version,fr.created_at,fr.updated_at,"
            "fr.parent_flow_run_id,fr.parent_task_run_id,fr.root_flow_run_id,"
            "fr.execution_mode,fr.depth,fr.flow_id FROM flow_runs fr "
            "LEFT JOIN flows catalog ON catalog.id = fr.flow_id"
        )
        conditions: list[str] = []
        params: list[Any] = []
        if state:
            conditions.append("fr.state = ?")
            params.append(state)
        if hide:
            conditions.append(
                "(catalog.id IS NULL OR catalog.status = 'active')"
            )
        else:
            conditions.append(
                "(catalog.id IS NULL OR catalog.status IN ('active','archived'))"
            )
        if cursor:
            conditions.append("fr.seq < ?")
            params.append(int(cursor))
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY fr.seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._flow_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def _attach_graph_mode_fields_to_detail(
        self, result: dict[str, Any], flow_run_id: UUID
    ) -> None:
        rec = self._flows.get(flow_run_id)
        if rec is not None:
            if rec.resume_from_flow_run_id is not None:
                result["resume_from_flow_run_id"] = str(rec.resume_from_flow_run_id)
            if rec.resume_lineage_id is not None:
                result["resume_lineage_id"] = str(rec.resume_lineage_id)
            result["declared_graph_mode"] = rec.declared_graph_mode
            result["effective_graph_mode"] = rec.effective_graph_mode
            if rec.manifest_fingerprint:
                result["manifest_fingerprint"] = rec.manifest_fingerprint
            if rec.contract_mismatch:
                result["contract_mismatch"] = True
            result["flow_attempt_number"] = rec.flow_attempt_number
            return
        extra = self._query_rows(
            "SELECT resume_from_flow_run_id, resume_lineage_id, declared_graph_mode, "
            "effective_graph_mode, manifest_fingerprint, contract_mismatch, flow_attempt_number "
            "FROM flow_runs WHERE id = ? LIMIT 1",
            [str(flow_run_id)],
        )
        if not extra:
            return
        row = extra[0]
        if row["resume_from_flow_run_id"]:
            result["resume_from_flow_run_id"] = row["resume_from_flow_run_id"]
        if row["resume_lineage_id"]:
            result["resume_lineage_id"] = row["resume_lineage_id"]
        if row["declared_graph_mode"]:
            result["declared_graph_mode"] = row["declared_graph_mode"]
        if row["effective_graph_mode"]:
            result["effective_graph_mode"] = row["effective_graph_mode"]
        if row["manifest_fingerprint"]:
            result["manifest_fingerprint"] = row["manifest_fingerprint"]
        if int(row["contract_mismatch"] or 0):
            result["contract_mismatch"] = True
        if row["flow_attempt_number"] is not None:
            result["flow_attempt_number"] = int(row["flow_attempt_number"])

    def _attach_deployment_fields_to_detail(
        self, result: dict[str, Any], flow_run_id: UUID
    ) -> None:
        dep_rows = self._query_rows(
            "SELECT deployment_id FROM deployment_runs WHERE flow_run_id = ? ORDER BY created_at DESC LIMIT 1",
            [str(flow_run_id)],
        )
        if not dep_rows:
            return
        result["deployment_id"] = dep_rows[0]["deployment_id"]
        param_rows = self._query_rows(
            "SELECT resolved_parameters FROM deployment_runs "
            "WHERE flow_run_id = ? ORDER BY created_at DESC LIMIT 1",
            [str(flow_run_id)],
        )
        if not param_rows:
            return
        try:
            result["parameters"] = json.loads(
                param_rows[0]["resolved_parameters"] or "{}"
            )
        except (TypeError, json.JSONDecodeError):
            result["parameters"] = {}

    def _attach_lifecycle_fields_to_detail(
        self, result: dict[str, Any], flow_run_id: UUID
    ) -> None:
        life = self._lifecycle_by_flow.get(str(flow_run_id))
        if life:
            result["lifecycle_action"] = life.get("lifecycle_action")
            result["interrupt_mode"] = life.get("interrupt_mode")
            if life.get("pause_drain_pending"):
                result["pause_drain_pending"] = True
            summary = life.get("lifecycle_summary")
            if summary:
                result["lifecycle_summary"] = summary
        else:
            result.setdefault("lifecycle_action", None)
            result.setdefault("interrupt_mode", None)

    def get_flow_run_detail(self, flow_run_id: UUID) -> dict[str, Any] | None:
        rust_result = self._query_rust(
            "flow_run_detail", {"flow_run_id": str(flow_run_id)}
        )
        if rust_result is not None:
            result = rust_result
        else:
            rows = self._query_rows(
                "SELECT seq,id,name,state,version,created_at,updated_at,parent_flow_run_id,parent_task_run_id,"
                "root_flow_run_id,execution_mode,depth FROM flow_runs WHERE id = ? LIMIT 1",
                [str(flow_run_id)],
            )
            if not rows:
                return None
            result = self._flow_row_to_dict(rows[0])
        self._attach_deployment_fields_to_detail(result, flow_run_id)
        self._attach_graph_mode_fields_to_detail(result, flow_run_id)
        result["breadcrumb"] = self._flow_run_breadcrumb(flow_run_id)
        result["children_summary"] = self._flow_run_children_summary(flow_run_id)
        result["children"] = self._flow_run_children(flow_run_id)
        self._attach_lifecycle_fields_to_detail(result, flow_run_id)
        return result

    def list_task_runs(
        self, flow_run_id: UUID, limit: int = 200, cursor: str | None = None
    ) -> PageResult:
        rust_result = self._query_rust(
            "task_runs",
            {"flow_run_id": str(flow_run_id), "limit": limit, "cursor": cursor},
        )
        if rust_result is not None:
            return PageResult(
                items=rust_result["items"], next_cursor=rust_result["next_cursor"]
            )
        query = (
            "SELECT seq,id,flow_run_id,task_name,planned_node_id,state,version,created_at,updated_at,"
            "kind,child_flow_run_id,child_deployment_run_id,task_run_attempt "
            "FROM task_runs WHERE flow_run_id = ?"
        )
        params: list[Any] = [str(flow_run_id)]
        if cursor:
            query += " AND seq < ?"
            params.append(int(cursor))
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._task_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def list_logs(
        self,
        flow_run_id: UUID,
        task_run_id: UUID | None = None,
        level: str | None = None,
        limit: int = 500,
        cursor: str | None = None,
    ) -> PageResult:
        rust_result = self._query_rust(
            "logs",
            {
                "flow_run_id": str(flow_run_id),
                "task_run_id": str(task_run_id) if task_run_id else None,
                "level": level.upper() if level else None,
                "limit": limit,
                "cursor": cursor,
            },
        )
        if rust_result is not None:
            return PageResult(
                items=rust_result["items"], next_cursor=rust_result["next_cursor"]
            )
        query = (
            "SELECT seq,id,flow_run_id,task_run_id,level,message,timestamp "
            "FROM logs WHERE flow_run_id = ?"
        )
        params: list[Any] = [str(flow_run_id)]
        if task_run_id:
            query += " AND task_run_id = ?"
            params.append(str(task_run_id))
        if level:
            query += " AND level = ?"
            params.append(level.upper())
        if cursor:
            query += " AND seq < ?"
            params.append(int(cursor))
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._log_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def list_flows(self, limit: int = 200, cursor: str | None = None) -> PageResult:
        rust_result = self._query_rust("flows", {"limit": limit, "cursor": cursor})
        if rust_result is not None:
            return PageResult(
                items=rust_result["items"], next_cursor=rust_result["next_cursor"]
            )
        query = (
            "SELECT seq,name,MAX(updated_at) AS updated_at,COUNT(*) AS run_count "
            "FROM flow_runs"
        )
        params: list[Any] = []
        if cursor:
            query += " WHERE seq < ?"
            params.append(int(cursor))
        query += " GROUP BY name ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [
            {
                "name": row["name"],
                "run_count": row["run_count"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def list_tasks(
        self, flow_name: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        rust_result = self._query_rust(
            "tasks", {"flow_name": flow_name, "limit": limit}
        )
        if rust_result is not None:
            return rust_result
        query = (
            "SELECT tr.task_name AS task_name, COUNT(*) AS run_count, MAX(tr.updated_at) AS updated_at "
            "FROM task_runs tr "
            "JOIN flow_runs fr ON fr.id = tr.flow_run_id"
        )
        params: list[Any] = []
        if flow_name:
            query += " WHERE fr.name = ?"
            params.append(flow_name)
        query += " GROUP BY tr.task_name ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        return [
            {
                "task_name": row["task_name"],
                "run_count": row["run_count"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def list_events(
        self, flow_run_id: UUID, limit: int = 500, cursor: str | None = None
    ) -> PageResult:
        rust_result = self._query_rust(
            "events",
            {"flow_run_id": str(flow_run_id), "limit": limit, "cursor": cursor},
        )
        if rust_result is not None:
            return PageResult(
                items=rust_result["items"], next_cursor=rust_result["next_cursor"]
            )
        query = (
            "SELECT seq,event_id,run_id,task_run_id,from_state,to_state,event_type,kind,data,timestamp "
            "FROM events WHERE run_id = ?"
        )
        params: list[Any] = [str(flow_run_id)]
        if cursor:
            query += " AND seq < ?"
            params.append(int(cursor))
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._event_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def list_artifacts_for_flow(
        self, flow_run_id: UUID, limit: int = 200
    ) -> list[dict[str, Any]]:
        rust_result = self._query_rust(
            "artifacts_flow", {"flow_run_id": str(flow_run_id), "limit": limit}
        )
        if rust_result is not None:
            return rust_result
        rows = self._query_rows(
            "SELECT id,flow_run_id,task_run_id,artifact_type,key,summary,created_at "
            "FROM artifacts WHERE flow_run_id = ? ORDER BY created_at DESC LIMIT ?",
            [str(flow_run_id), limit],
        )
        return [self._artifact_row_to_dict(r) for r in rows]

    def list_artifacts_for_task(
        self, task_run_id: UUID, limit: int = 200
    ) -> list[dict[str, Any]]:
        rust_result = self._query_rust(
            "artifacts_task", {"task_run_id": str(task_run_id), "limit": limit}
        )
        if rust_result is not None:
            return rust_result
        rows = self._query_rows(
            "SELECT id,flow_run_id,task_run_id,artifact_type,key,summary,created_at "
            "FROM artifacts WHERE task_run_id = ? ORDER BY created_at DESC LIMIT ?",
            [str(task_run_id), limit],
        )
        return [self._artifact_row_to_dict(r) for r in rows]

    def get_artifact(self, artifact_id: UUID) -> dict[str, Any] | None:
        rust_result = self._query_rust("artifact", {"artifact_id": str(artifact_id)})
        if rust_result is not None:
            return rust_result
        rows = self._query_rows(
            "SELECT id,flow_run_id,task_run_id,artifact_type,key,summary,created_at "
            "FROM artifacts WHERE id = ? LIMIT 1",
            [str(artifact_id)],
        )
        return self._artifact_row_to_dict(rows[0]) if rows else None
