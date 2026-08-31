from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import UUID

from .types import SUBFLOW_MAX_DEPTH


class DagMixin:
    def get_flow_run_dag(
        self, flow_run_id: UUID, mode: str = "logical"
    ) -> dict[str, Any]:
        manifest_rows = self._query_rows(
            "SELECT manifest_json, forecast_json, warnings_json, fallback_required, source FROM dag_manifests WHERE flow_run_id = ? LIMIT 1",
            [str(flow_run_id)],
        )
        task_rows = self._task_rows_for_dag(flow_run_id)

        if manifest_rows:
            manifest_raw = manifest_rows[0]
            manifest = json.loads(manifest_raw["manifest_json"] or "{}")
            forecast = json.loads(manifest_raw["forecast_json"] or "{}")
            warnings = json.loads(manifest_raw["warnings_json"] or "[]")
            fallback_required = bool(manifest_raw["fallback_required"])
            source = manifest_raw["source"]
        else:
            manifest = {"nodes": [], "edges": []}
            forecast = {}
            warnings = ["No precomputed forecast manifest available for run."]
            fallback_required = True
            source = "runtime"

        if not manifest.get("nodes"):
            manifest = self._infer_runtime_manifest(task_rows)
            source = "runtime"
            fallback_required = True
            warnings = warnings + ["Using runtime-inferred DAG."]

        if mode == "expanded":
            nodes, edges = self._expanded_dag(manifest, task_rows)
        else:
            nodes, edges = self._logical_dag(manifest, task_rows)
        nodes, edges = self._enrich_dag_subflow_nodes(
            flow_run_id, nodes, edges, task_rows
        )
        return {
            "flow_run_id": str(flow_run_id),
            "mode": mode,
            "source": source,
            "fallback_required": fallback_required,
            "warnings": warnings,
            "forecast": forecast,
            "nodes": nodes,
            "edges": edges,
        }

    def _infer_runtime_manifest(
        self, task_rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []
        instance_counts: dict[str, int] = {}
        node_by_planned: dict[str, str] = {}
        previous: str | None = None
        counter = 0

        for row in task_rows:
            task_name = row["task_name"]
            planned = row["planned_node_id"]
            node_id = str(planned) if planned else None

            if node_id is None or node_id not in node_by_planned:
                counter += 1
                node_id = node_id or f"rt_{counter}"
                instance = instance_counts.get(task_name, 0)
                instance_counts[task_name] = instance + 1
                node_by_planned[node_id] = node_id
                nodes.append(
                    {
                        "node_id": node_id,
                        "task_name": task_name,
                        "label": f"{task_name}-{instance}",
                        "op_type": "runtime",
                        "deps": [],
                    }
                )
                if previous is not None:
                    edges.append({"from": previous, "to": node_id})
                previous = node_id

        return {"nodes": nodes, "edges": edges}

    def _logical_dag(
        self, manifest: dict[str, Any], task_rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        nodes = manifest.get("nodes", [])
        edges = manifest.get("edges", [])
        by_planned: dict[str, list[str]] = {}
        meta_by_planned: dict[str, dict[str, Any]] = {}
        for row in task_rows:
            state = str(row["state"])
            planned = row.get("planned_node_id")
            if planned:
                key = str(planned)
                by_planned.setdefault(key, []).append(state)
                meta_by_planned[key] = row
            task_name = str(row["task_name"])
            if task_name.startswith("subflow:"):
                key = str(planned or row["id"])
                by_planned.setdefault(key, []).append(state)
                meta_by_planned[key] = row

        out_nodes: list[dict[str, Any]] = []
        node_state: dict[str, str] = {}
        for node in nodes:
            node_id = str(node["node_id"])
            states = by_planned.get(node_id, [])
            state = self._aggregate_state(states)
            node_state[node_id] = state
            label = node.get("label") or node.get("task_name", node_id)
            out_node: dict[str, Any] = {
                "id": node_id,
                "label": label,
                "task_name": node.get("task_name"),
                "op_type": node.get("op_type"),
                "planned_node_id": node_id,
                "state": state,
                "kind": "gate_task" if node.get("op_type") == "gate" else "task",
            }
            meta = meta_by_planned.get(node_id)
            task_name_meta = str(meta.get("task_name", "")) if meta is not None else ""
            is_subflow_meta = meta is not None and (
                str(meta.get("kind", "task")) == "subflow"
                or task_name_meta.startswith("subflow:")
            )
            if is_subflow_meta:
                out_node["kind"] = "subflow_task"
                if meta.get("child_flow_run_id"):
                    out_node["child_flow_run_id"] = str(meta["child_flow_run_id"])
                if meta.get("child_deployment_run_id"):
                    out_node["child_deployment_run_id"] = str(
                        meta["child_deployment_run_id"]
                    )
                if task_name_meta.startswith("subflow:"):
                    out_node["label"] = task_name_meta.removeprefix("subflow:")
            elif meta is not None and (
                str(meta.get("kind", "task")) == "gate"
                or task_name_meta.startswith("gate:")
            ):
                out_node["kind"] = "gate_task"
                if meta.get("gate_open_at"):
                    out_node["gate_open_at"] = str(meta["gate_open_at"])
                if task_name_meta.startswith("gate:"):
                    out_node["label"] = task_name_meta.removeprefix("gate:")
            out_nodes.append(out_node)

        upstreams: dict[str, list[str]] = {}
        for edge in edges:
            upstreams.setdefault(edge["to"], []).append(edge["from"])
        for node in out_nodes:
            if node["state"] in {"PENDING", "SCHEDULED"}:
                if any(
                    node_state.get(src) in {"FAILED", "CANCELLED", "NOT_REACHABLE"}
                    for src in upstreams.get(node["id"], [])
                ):
                    node["state"] = "NOT_REACHABLE"
                    node_state[node["id"]] = "NOT_REACHABLE"
        out_nodes, edges = self._prune_orphan_forecast_nodes(
            out_nodes, edges, meta_by_planned
        )
        return out_nodes, edges

    def _prune_orphan_forecast_nodes(
        self,
        out_nodes: list[dict[str, Any]],
        edges: list[dict[str, str]],
        meta_by_planned: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        drop_ids: set[str] = set()
        for node in out_nodes:
            if str(node.get("task_name", "")) != "unknown_task":
                continue
            node_id = str(node["id"])
            if node_id not in meta_by_planned and node.get("state") == "PENDING":
                drop_ids.add(node_id)
        if not drop_ids:
            return out_nodes, edges
        pruned_nodes = [node for node in out_nodes if str(node["id"]) not in drop_ids]
        pruned_edges = [
            edge
            for edge in edges
            if edge["from"] not in drop_ids and edge["to"] not in drop_ids
        ]
        return pruned_nodes, pruned_edges

    def _expanded_dag(
        self, manifest: dict[str, Any], task_rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        max_nodes = 600
        limited_rows = task_rows[:max_nodes]
        nodes: list[dict[str, Any]] = []
        for row in limited_rows:
            kind = str(row.get("kind", "task"))
            task_name = str(row["task_name"])
            label = f"{task_name}:{str(row['id'])[:8]}"
            node: dict[str, Any] = {
                "id": row["id"],
                "label": label,
                "task_name": task_name,
                "planned_node_id": row.get("planned_node_id"),
                "state": row["state"],
                "kind": "subflow_task"
                if kind == "subflow"
                else "gate_task"
                if kind == "gate"
                else "task",
            }
            if kind == "subflow":
                if row.get("child_flow_run_id"):
                    node["child_flow_run_id"] = row["child_flow_run_id"]
                if row.get("child_deployment_run_id"):
                    node["child_deployment_run_id"] = row["child_deployment_run_id"]
                if task_name.startswith("subflow:"):
                    node["label"] = (
                        f"{task_name.removeprefix('subflow:')}:{str(row['id'])[:8]}"
                    )
            if kind == "gate":
                if row.get("gate_open_at"):
                    node["gate_open_at"] = str(row["gate_open_at"])
                if task_name.startswith("gate:"):
                    node["label"] = (
                        f"{task_name.removeprefix('gate:')}:{str(row['id'])[:8]}"
                    )
            nodes.append(node)
        by_planned_runs: dict[str, list[str]] = {}
        for row in limited_rows:
            if row["planned_node_id"]:
                by_planned_runs.setdefault(row["planned_node_id"], []).append(row["id"])
        manifest_edges = manifest.get("edges", [])
        edges: list[dict[str, str]] = []
        for edge in manifest_edges:
            src_runs = by_planned_runs.get(edge["from"], [])
            dst_runs = by_planned_runs.get(edge["to"], [])
            for src in src_runs:
                for dst in dst_runs:
                    edges.append({"from": src, "to": dst})
                    if len(edges) >= 2000:
                        return nodes, edges
        if not edges:
            for i in range(1, len(nodes)):
                edges.append({"from": nodes[i - 1]["id"], "to": nodes[i]["id"]})
        return nodes, edges

    def _task_rows_for_dag(self, flow_run_id: UUID) -> list[dict[str, Any]]:
        rows = self._query_rows(
            """
            SELECT id, task_name, planned_node_id, state, created_at, updated_at,
                   kind, child_flow_run_id, child_deployment_run_id, gate_open_at
            FROM task_runs
            WHERE flow_run_id = ?
            ORDER BY created_at ASC
            """,
            [str(flow_run_id)],
        )
        by_id = {str(row["id"]): self._dag_task_row_dict(row) for row in rows}
        with self._lock:
            memory_tasks = [
                task for task in self._tasks.values() if task.flow_run_id == flow_run_id
            ]
        for task in memory_tasks:
            tid = str(task.task_run_id)
            existing = by_id.get(tid)
            if existing is None:
                by_id[tid] = {
                    "id": tid,
                    "flow_run_id": str(task.flow_run_id),
                    "task_name": task.task_name,
                    "planned_node_id": task.planned_node_id,
                    "state": task.state.value,
                    "version": task.version,
                    "created_at": self._now(),
                    "updated_at": self._now(),
                    "kind": task.kind,
                    "child_flow_run_id": str(task.child_flow_run_id)
                    if task.child_flow_run_id
                    else None,
                    "child_deployment_run_id": str(task.child_deployment_run_id)
                    if task.child_deployment_run_id
                    else None,
                    "gate_open_at": task.gate_open_at,
                }
                continue
            if task.kind != "task":
                existing["kind"] = task.kind
            if task.gate_open_at and not existing.get("gate_open_at"):
                existing["gate_open_at"] = task.gate_open_at
            if task.child_flow_run_id and not existing.get("child_flow_run_id"):
                existing["child_flow_run_id"] = str(task.child_flow_run_id)
            if task.child_deployment_run_id and not existing.get(
                "child_deployment_run_id"
            ):
                existing["child_deployment_run_id"] = str(task.child_deployment_run_id)
        return sorted(by_id.values(), key=lambda item: item["created_at"])

    def _dag_task_row_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        return {
            "id": row["id"],
            "task_name": row["task_name"],
            "planned_node_id": row["planned_node_id"]
            if "planned_node_id" in keys
            else None,
            "state": row["state"],
            "created_at": row["created_at"] if "created_at" in keys else self._now(),
            "updated_at": row["updated_at"] if "updated_at" in keys else self._now(),
            "kind": row["kind"] if "kind" in keys else "task",
            "child_flow_run_id": row["child_flow_run_id"]
            if "child_flow_run_id" in keys
            else None,
            "child_deployment_run_id": row["child_deployment_run_id"]
            if "child_deployment_run_id" in keys
            else None,
            "gate_open_at": row["gate_open_at"] if "gate_open_at" in keys else None,
        }

    def _resolve_subflow_child_flow_run_id(
        self, task_row: dict[str, Any]
    ) -> str | None:
        child_flow = task_row.get("child_flow_run_id")
        if child_flow:
            return str(child_flow)
        task_id = task_row.get("id")
        if task_id:
            with self._lock:
                task = self._tasks.get(UUID(str(task_id)))
                if task and task.child_flow_run_id:
                    return str(task.child_flow_run_id)
        dep_run_id = task_row.get("child_deployment_run_id")
        if not dep_run_id and task_id:
            with self._lock:
                task = self._tasks.get(UUID(str(task_id)))
                if task and task.child_deployment_run_id:
                    dep_run_id = str(task.child_deployment_run_id)
        if dep_run_id:
            dep = self.get_deployment_run(UUID(str(dep_run_id)))
            if dep and dep.get("flow_run_id"):
                return str(dep["flow_run_id"])
        if task_id:
            dep_rows = self._query_rows(
                """
                SELECT flow_run_id
                FROM deployment_runs
                WHERE parent_task_run_id = ? AND flow_run_id IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                [str(task_id)],
            )
            if dep_rows and dep_rows[0]["flow_run_id"]:
                return str(dep_rows[0]["flow_run_id"])
            child_rows = self._query_rows(
                """
                SELECT id
                FROM flow_runs
                WHERE parent_task_run_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                [str(task_id)],
            )
            if child_rows:
                return str(child_rows[0]["id"])
        return None

    def _resolve_subflow_child_deployment_run_id(
        self, task_row: dict[str, Any]
    ) -> str | None:
        dep_run_id = task_row.get("child_deployment_run_id")
        if dep_run_id:
            return str(dep_run_id)
        task_id = task_row.get("id")
        if task_id:
            with self._lock:
                task = self._tasks.get(UUID(str(task_id)))
                if task and task.child_deployment_run_id:
                    return str(task.child_deployment_run_id)
            dep_rows = self._query_rows(
                """
                SELECT id
                FROM deployment_runs
                WHERE parent_task_run_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                [str(task_id)],
            )
            if dep_rows:
                return str(dep_rows[0]["id"])
        return None

    def _enrich_dag_subflow_nodes(
        self,
        flow_run_id: UUID,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, str]],
        task_rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        node_by_id = {str(node["id"]): node for node in nodes}
        for row in task_rows:
            kind = str(row.get("kind", "task"))
            task_name = str(row["task_name"])
            is_subflow = kind == "subflow" or task_name.startswith("subflow:")
            if not is_subflow:
                continue
            planned = (
                str(row["planned_node_id"]) if row.get("planned_node_id") else None
            )
            node_id = planned or str(row["id"])
            node = node_by_id.get(node_id)
            if node is None and planned:
                node = next(
                    (n for n in nodes if n.get("planned_node_id") == planned), None
                )
            if node is None:
                label = (
                    task_name.removeprefix("subflow:")
                    if task_name.startswith("subflow:")
                    else task_name
                )
                node = {
                    "id": node_id,
                    "label": label,
                    "task_name": task_name,
                    "planned_node_id": row.get("planned_node_id"),
                    "state": row["state"],
                    "kind": "subflow_task",
                }
                nodes.append(node)
                node_by_id[node_id] = node
                if nodes and len(nodes) > 1:
                    prev_id = str(nodes[-2]["id"])
                    if prev_id != node_id:
                        edges.append({"from": prev_id, "to": node_id})
            node["kind"] = "subflow_task"
            child_flow_run_id = self._resolve_subflow_child_flow_run_id(row)
            if child_flow_run_id:
                node["child_flow_run_id"] = child_flow_run_id
            dep_run_id = self._resolve_subflow_child_deployment_run_id(row)
            if dep_run_id:
                node["child_deployment_run_id"] = dep_run_id

        inline_rows = self._query_rows(
            """
            SELECT id, name, state
            FROM flow_runs
            WHERE parent_flow_run_id = ? AND execution_mode = 'inline'
            ORDER BY created_at ASC
            """,
            [str(flow_run_id)],
        )
        previous_id: str | None = str(nodes[-1]["id"]) if nodes else None
        for row in inline_rows:
            child_id = str(row["id"])
            node_id = f"inline:{child_id}"
            if node_id in node_by_id:
                continue
            inline_node = {
                "id": node_id,
                "label": row["name"],
                "task_name": row["name"],
                "kind": "inline_subflow",
                "child_flow_run_id": child_id,
                "execution_mode": "inline",
                "state": row["state"],
            }
            nodes.append(inline_node)
            node_by_id[node_id] = inline_node
            if previous_id is not None:
                edges.append({"from": previous_id, "to": node_id})
            previous_id = node_id
        return nodes, edges

    def _flow_run_breadcrumb(self, flow_run_id: UUID) -> list[dict[str, Any]]:
        chain: list[dict[str, Any]] = []
        current: UUID | None = flow_run_id
        visited: set[UUID] = set()
        for _ in range(SUBFLOW_MAX_DEPTH + 1):
            if current is None or current in visited:
                break
            visited.add(current)
            rows = self._query_rows(
                "SELECT id, name, parent_flow_run_id, execution_mode FROM flow_runs WHERE id = ? LIMIT 1",
                [str(current)],
            )
            if not rows:
                break
            row = rows[0]
            chain.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "execution_mode": row["execution_mode"]
                    if "execution_mode" in row.keys()
                    else None,
                }
            )
            parent = (
                row["parent_flow_run_id"]
                if "parent_flow_run_id" in row.keys()
                else None
            )
            current = UUID(str(parent)) if parent else None
        chain.reverse()
        return chain

    def _flow_run_children_summary(self, flow_run_id: UUID) -> dict[str, int]:
        inline_rows = self._query_rows(
            "SELECT COUNT(*) AS c FROM flow_runs WHERE parent_flow_run_id = ? AND execution_mode = 'inline'",
            [str(flow_run_id)],
        )
        subflow_rows = self._query_rows(
            "SELECT COUNT(*) AS c FROM task_runs WHERE flow_run_id = ? AND kind = 'subflow'",
            [str(flow_run_id)],
        )
        deployment_rows = self._query_rows(
            "SELECT COUNT(*) AS c FROM flow_runs WHERE parent_flow_run_id = ? AND execution_mode = 'deployment'",
            [str(flow_run_id)],
        )
        return {
            "inline_subflows": int(inline_rows[0]["c"]) if inline_rows else 0,
            "subflow_tasks": int(subflow_rows[0]["c"]) if subflow_rows else 0,
            "deployment_subflows": int(deployment_rows[0]["c"])
            if deployment_rows
            else 0,
        }

    def _flow_run_children(self, flow_run_id: UUID) -> list[dict[str, Any]]:
        rows = self._query_rows(
            """
            SELECT id, name, state, execution_mode, depth, created_at, updated_at
            FROM flow_runs
            WHERE parent_flow_run_id = ?
            ORDER BY created_at ASC
            """,
            [str(flow_run_id)],
        )
        children: list[dict[str, Any]] = []
        for row in rows:
            keys = row.keys()
            children.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "state": row["state"],
                    "execution_mode": row["execution_mode"]
                    if "execution_mode" in keys
                    else None,
                    "depth": int(row["depth"])
                    if "depth" in keys and row["depth"] is not None
                    else 0,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return children

    def _aggregate_state(self, states: list[str]) -> str:
        if not states:
            return "PENDING"
        priority = [
            "FAILED",
            "CANCELLED",
            "RUNNING",
            "PENDING",
            "SCHEDULED",
            "COMPLETED",
        ]
        for state in priority:
            if state in states:
                return state
        return states[-1]
