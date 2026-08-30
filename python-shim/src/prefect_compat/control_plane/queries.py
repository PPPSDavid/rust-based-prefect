from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from .types import (
    PageResult,
)


class QueriesMixin:
    def list_flow_runs(
        self, state: str | None = None, limit: int = 50, cursor: str | None = None
    ) -> PageResult:
        rust_result = self._query_rust(
            "flow_runs", {"state": state, "limit": limit, "cursor": cursor}
        )
        if rust_result is not None:
            return PageResult(
                items=rust_result["items"], next_cursor=rust_result["next_cursor"]
            )
        query = (
            "SELECT seq,id,name,state,version,created_at,updated_at,parent_flow_run_id,parent_task_run_id,"
            "root_flow_run_id,execution_mode,depth FROM flow_runs"
        )
        conditions: list[str] = []
        params: list[Any] = []
        if state:
            conditions.append("state = ?")
            params.append(state)
        if cursor:
            conditions.append("seq < ?")
            params.append(int(cursor))
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._flow_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

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
        dep_rows = self._query_rows(
            "SELECT deployment_id FROM deployment_runs WHERE flow_run_id = ? ORDER BY created_at DESC LIMIT 1",
            [str(flow_run_id)],
        )
        if dep_rows:
            result["deployment_id"] = dep_rows[0]["deployment_id"]
            param_rows = self._query_rows(
                "SELECT resolved_parameters FROM deployment_runs "
                "WHERE flow_run_id = ? ORDER BY created_at DESC LIMIT 1",
                [str(flow_run_id)],
            )
            if param_rows:
                try:
                    result["parameters"] = json.loads(
                        param_rows[0]["resolved_parameters"] or "{}"
                    )
                except (TypeError, json.JSONDecodeError):
                    result["parameters"] = {}
        rec = self._flows.get(flow_run_id)
        if rec is not None:
            if rec.resume_from_flow_run_id is not None:
                result["resume_from_flow_run_id"] = str(rec.resume_from_flow_run_id)
            if rec.resume_lineage_id is not None:
                result["resume_lineage_id"] = str(rec.resume_lineage_id)
        else:
            extra = self._query_rows(
                "SELECT resume_from_flow_run_id, resume_lineage_id "
                "FROM flow_runs WHERE id = ? LIMIT 1",
                [str(flow_run_id)],
            )
            if extra:
                if extra[0]["resume_from_flow_run_id"]:
                    result["resume_from_flow_run_id"] = extra[0][
                        "resume_from_flow_run_id"
                    ]
                if extra[0]["resume_lineage_id"]:
                    result["resume_lineage_id"] = extra[0]["resume_lineage_id"]
        result["breadcrumb"] = self._flow_run_breadcrumb(flow_run_id)
        result["children_summary"] = self._flow_run_children_summary(flow_run_id)
        result["children"] = self._flow_run_children(flow_run_id)
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
            "kind,child_flow_run_id,child_deployment_run_id "
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
