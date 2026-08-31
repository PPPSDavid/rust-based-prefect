from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from ..persistence import DEFAULT_WORK_POOL_ID
from .types import (
    PageResult,
    RunState,
)


class DeploymentRunsMixin:
    _DEPLOYMENT_TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
    _DEPLOYMENT_ACTIVE = frozenset({"SCHEDULED", "CLAIMED", "RUNNING"})

    def get_deployment_run(self, deployment_run_id: UUID) -> dict[str, Any] | None:
        rust = self._rust_deployment_dispatch(
            "deployment_get_run", {"deployment_run_id": str(deployment_run_id)}
        )
        if rust is not None:
            if rust.get("ok"):
                run = rust.get("run")
                if run is not None:
                    return self._merge_resume_from_flow_run_id(run)
            else:
                err = rust.get("error") or {}
                raise RuntimeError(str(err.get("message", "deployment get run failed")))
        with self._lock:
            self._ensure_resume_schema()
        rows = self._query_rows(
            """
            SELECT seq,id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
                   worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,
                   resume_from_flow_run_id,
                   created_at,updated_at,started_at,finished_at
            FROM deployment_runs
            WHERE id = ?
            LIMIT 1
            """,
            [str(deployment_run_id)],
        )
        if not rows:
            return None
        return self._deployment_run_row_to_dict(rows[0])

    def update_subflow_task_linkage(
        self,
        task_run_id: UUID,
        *,
        child_flow_run_id: UUID | None = None,
        child_deployment_run_id: UUID | None = None,
    ) -> None:
        if child_flow_run_id is None and child_deployment_run_id is None:
            return
        changed = False
        with self._lock:
            task = self._tasks.get(task_run_id)
            if task is None:
                return
            updates: list[str] = []
            params: list[str] = []
            if (
                child_flow_run_id is not None
                and task.child_flow_run_id != child_flow_run_id
            ):
                task.child_flow_run_id = child_flow_run_id
                updates.append("child_flow_run_id = ?")
                params.append(str(child_flow_run_id))
                changed = True
            if (
                child_deployment_run_id is not None
                and task.child_deployment_run_id != child_deployment_run_id
            ):
                task.child_deployment_run_id = child_deployment_run_id
                updates.append("child_deployment_run_id = ?")
                params.append(str(child_deployment_run_id))
                changed = True
            if updates:
                params.append(str(task_run_id))
                self._sqlite_conn.execute(
                    f"UPDATE task_runs SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
        if changed:
            self._persist_task_subflow_linkage(
                task_run_id, child_flow_run_id, child_deployment_run_id
            )

    def _persist_task_subflow_linkage(
        self,
        task_run_id: UUID,
        child_flow_run_id: UUID | None,
        child_deployment_run_id: UUID | None,
    ) -> None:
        self._persist_record(
            {
                "record_type": "task_subflow_linkage",
                "task_run_id": str(task_run_id),
                "child_flow_run_id": str(child_flow_run_id)
                if child_flow_run_id
                else None,
                "child_deployment_run_id": str(child_deployment_run_id)
                if child_deployment_run_id
                else None,
            }
        )

    def update_subflow_task_child_flow_run(
        self, task_run_id: UUID, child_flow_run_id: UUID
    ) -> None:
        self.update_subflow_task_linkage(
            task_run_id, child_flow_run_id=child_flow_run_id
        )

    def mirror_subflow_task_from_deployment(
        self, task_run_id: UUID, deployment_run: dict[str, Any]
    ) -> None:
        status = str(deployment_run.get("status", ""))
        dep_run_id = deployment_run.get("id")
        child_flow_run_id = deployment_run.get("flow_run_id")
        linkage_kwargs: dict[str, UUID] = {}
        if dep_run_id:
            linkage_kwargs["child_deployment_run_id"] = UUID(str(dep_run_id))
        if child_flow_run_id:
            linkage_kwargs["child_flow_run_id"] = UUID(str(child_flow_run_id))
        if linkage_kwargs:
            self.update_subflow_task_linkage(task_run_id, **linkage_kwargs)

        task = self.get_task_run(task_run_id)
        if status in {"SCHEDULED", "CLAIMED"} and task.state == RunState.SCHEDULED:
            self.record_task_event(task_run_id, "task_pending", {"subflow": True})
        elif status == "RUNNING" and task.state in {
            RunState.SCHEDULED,
            RunState.PENDING,
        }:
            if task.state == RunState.SCHEDULED:
                self.record_task_event(task_run_id, "task_pending", {"subflow": True})
            self.record_task_event(task_run_id, "task_running", {"subflow": True})
        elif status == "COMPLETED" and task.state not in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            if task.state == RunState.SCHEDULED:
                self.record_task_event(task_run_id, "task_pending", {"subflow": True})
            if task.state in {RunState.SCHEDULED, RunState.PENDING}:
                self.record_task_event(task_run_id, "task_running", {"subflow": True})
            self.record_task_event(task_run_id, "task_completed", {"subflow": True})
        elif status == "FAILED" and task.state not in {
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            if task.state == RunState.SCHEDULED:
                self.record_task_event(task_run_id, "task_pending", {"subflow": True})
            if task.state in {RunState.SCHEDULED, RunState.PENDING}:
                self.record_task_event(task_run_id, "task_running", {"subflow": True})
            err = deployment_run.get("error") or "subflow deployment failed"
            self.record_task_event(
                task_run_id, "task_failed", {"subflow": True, "error": str(err)}
            )
        elif status == "CANCELLED" and task.state not in {
            RunState.CANCELLED,
            RunState.FAILED,
            RunState.COMPLETED,
        }:
            if task.state == RunState.SCHEDULED:
                self.record_task_event(task_run_id, "task_pending", {"subflow": True})
            if task.state in {RunState.SCHEDULED, RunState.PENDING}:
                self.record_task_event(task_run_id, "task_running", {"subflow": True})
            self.record_task_event(
                task_run_id,
                "task_cancelled",
                {"subflow": True, "error": deployment_run.get("error") or "cancelled"},
            )

    def wait_for_deployment_run_terminal(
        self,
        deployment_run_id: UUID,
        *,
        parent_task_run_id: UUID | None = None,
        timeout_seconds: float = 3600.0,
        poll_seconds: float = 0.05,
    ) -> dict[str, Any]:
        from ..cancellation import FlowRunCancelled, assert_flow_not_cancelled
        from ..decorators import _ACTIVE_FLOW_RUN

        deadline = time.monotonic() + max(0.0, timeout_seconds)
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            parent_flow_id = _ACTIVE_FLOW_RUN.get()
            if parent_flow_id is not None:
                try:
                    assert_flow_not_cancelled(parent_flow_id)
                except FlowRunCancelled:
                    raise
            last = self.get_deployment_run(deployment_run_id)
            if last is None:
                if time.monotonic() + poll_seconds < deadline:
                    time.sleep(poll_seconds)
                    continue
                raise ValueError(f"deployment run not found: {deployment_run_id}")
            if parent_task_run_id is not None:
                self.mirror_subflow_task_from_deployment(parent_task_run_id, last)
            status = str(last.get("status", ""))
            if status in self._DEPLOYMENT_TERMINAL:
                if parent_task_run_id is not None:
                    self.mirror_subflow_task_from_deployment(parent_task_run_id, last)
                return last
            time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        raise TimeoutError(f"timed out waiting for deployment run {deployment_run_id}")

    def trigger_deployment_run(
        self,
        deployment_id: UUID,
        parameters: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        *,
        parent_flow_run_id: UUID | None = None,
        parent_task_run_id: UUID | None = None,
        parent_deployment_run_id: UUID | None = None,
        resume_from_flow_run_id: UUID | None = None,
    ) -> dict[str, Any]:
        rust_body: dict[str, Any] = {
            "deployment_id": str(deployment_id),
            "parameters": parameters,
            "idempotency_key": idempotency_key,
        }
        if parent_flow_run_id is not None:
            rust_body["parent_flow_run_id"] = str(parent_flow_run_id)
        if parent_task_run_id is not None:
            rust_body["parent_task_run_id"] = str(parent_task_run_id)
        if parent_deployment_run_id is not None:
            rust_body["parent_deployment_run_id"] = str(parent_deployment_run_id)
        rust = self._rust_deployment_dispatch("deployment_trigger_run", rust_body)
        if rust is not None:
            if rust.get("ok"):
                run = rust["run"]
                if resume_from_flow_run_id is not None:
                    run_id = run.get("id") if isinstance(run, dict) else None
                    if not run_id:
                        raise ValueError(
                            "deployment trigger succeeded but returned no run id; "
                            "cannot attach resume_from_flow_run_id"
                        )
                    with self._lock:
                        self._ensure_resume_schema()
                        self._sqlite_conn.execute(
                            "UPDATE deployment_runs SET resume_from_flow_run_id = ? WHERE id = ?",
                            [str(resume_from_flow_run_id), str(run_id)],
                        )
                    run = dict(run)
                    run["resume_from_flow_run_id"] = str(resume_from_flow_run_id)
                return run
            err = rust.get("error") or {}
            code = err.get("code", "")
            msg = str(err.get("message", ""))
            if code == "not_found":
                raise ValueError("deployment not found")
            if code == "paused":
                raise ValueError("deployment is paused")
            raise ValueError(msg or "deployment trigger failed")

        with self._lock:
            dep = self.get_deployment(deployment_id)
            if dep is None:
                raise ValueError("deployment not found")
            if dep["paused"]:
                raise ValueError("deployment is paused")

            if idempotency_key:
                existing = self._query_rows(
                    """
                    SELECT seq,id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
                           worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,
                           created_at,updated_at,started_at,finished_at
                    FROM deployment_runs
                    WHERE deployment_id = ? AND idempotency_key = ?
                    LIMIT 1
                    """,
                    [str(deployment_id), idempotency_key],
                )
                if existing:
                    return self._deployment_run_row_to_dict(existing[0])

            requested = parameters or {}
            resolved = dict(dep.get("default_parameters", {}))
            resolved.update(requested)
            strategy = (dep.get("collision_strategy") or "ENQUEUE").upper()
            limit = dep.get("concurrency_limit")
            status = "SCHEDULED"
            err_msg: str | None = None
            if (
                limit is not None
                and strategy == "CANCEL_NEW"
                and self._count_exec_runs(str(deployment_id)) >= int(limit)
            ):
                status = "CANCELLED"
                err_msg = "concurrency limit reached"
            now = self._now()
            run_id = str(uuid4())
            # INSERT always names resume_from_flow_run_id; ensure column exists even
            # when resume is unset (common deployment / subflow trigger path).
            self._ensure_resume_schema()
            self._sqlite_conn.execute(
                """
                INSERT INTO deployment_runs
                (id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
                 worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,
                 resume_from_flow_run_id,
                 created_at,updated_at,started_at,finished_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    run_id,
                    str(deployment_id),
                    status,
                    json.dumps(requested),
                    json.dumps(resolved),
                    idempotency_key,
                    None,
                    None,
                    None,
                    err_msg,
                    str(parent_flow_run_id) if parent_flow_run_id else None,
                    str(parent_task_run_id) if parent_task_run_id else None,
                    str(parent_deployment_run_id) if parent_deployment_run_id else None,
                    str(resume_from_flow_run_id) if resume_from_flow_run_id else None,
                    now,
                    now,
                    None,
                    None,
                ],
            )
            row = self._query_rows(
                """
                SELECT seq,id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
                       worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,
                       resume_from_flow_run_id,
                       created_at,updated_at,started_at,finished_at
                FROM deployment_runs
                WHERE id = ?
                LIMIT 1
                """,
                [run_id],
            )[0]
            return self._deployment_run_row_to_dict(row)

    def list_deployment_runs(
        self,
        deployment_id: UUID | None = None,
        limit: int = 200,
        cursor: str | None = None,
    ) -> PageResult:
        with self._lock:
            self._ensure_resume_schema()
        query = (
            "SELECT seq,id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,"
            " worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,"
            " resume_from_flow_run_id,"
            " created_at,updated_at,started_at,finished_at "
            "FROM deployment_runs"
        )
        conditions: list[str] = []
        params: list[Any] = []
        if deployment_id:
            conditions.append("deployment_id = ?")
            params.append(str(deployment_id))
        if cursor:
            conditions.append("seq < ?")
            params.append(int(cursor))
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._deployment_run_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def _cancel_deployment_runs_for_parent_flow(
        self, parent_flow_run_id: UUID
    ) -> list[dict[str, Any]]:
        """Cancel SCHEDULED/CLAIMED/RUNNING deployment runs triggered from a parent flow."""
        rust = self._rust_deployment_dispatch(
            "deployment_cancel_by_parent_flow",
            {"parent_flow_run_id": str(parent_flow_run_id)},
        )
        if rust is not None:
            if rust.get("ok"):
                cancelled = rust.get("cancelled") or []
                for row in cancelled:
                    ptid = row.get("parent_task_run_id")
                    if ptid:
                        self.mirror_subflow_task_from_deployment(
                            UUID(str(ptid)),
                            {
                                **row,
                                "status": "CANCELLED",
                                "error": "parent flow cancelled",
                            },
                        )
                return list(cancelled)
            err = rust.get("error") or {}
            raise RuntimeError(str(err.get("message", "deployment cancel failed")))

        now = self._now()
        rows = self._query_rows(
            """
            SELECT id, flow_run_id, parent_task_run_id
            FROM deployment_runs
            WHERE parent_flow_run_id = ? AND status IN ('SCHEDULED','CLAIMED','RUNNING')
            """,
            [str(parent_flow_run_id)],
        )
        if not rows:
            return []
        self._sqlite_conn.execute(
            """
            UPDATE deployment_runs
            SET status = 'CANCELLED', error = 'parent flow cancelled', finished_at = ?, updated_at = ?, lease_until = NULL
            WHERE parent_flow_run_id = ? AND status IN ('SCHEDULED','CLAIMED','RUNNING')
            """,
            [now, now, str(parent_flow_run_id)],
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            item = {
                "id": row["id"],
                "flow_run_id": row["flow_run_id"],
                "parent_task_run_id": row["parent_task_run_id"],
                "status": "CANCELLED",
            }
            out.append(item)
            if row["parent_task_run_id"]:
                self.mirror_subflow_task_from_deployment(
                    UUID(str(row["parent_task_run_id"])),
                    {**item, "error": "parent flow cancelled"},
                )
        return out

    def list_work_pools(self, limit: int = 50, cursor: str | None = None) -> PageResult:
        query = "SELECT rowid AS seq, id, name, type, paused, created_at, updated_at FROM work_pools"
        params: list[Any] = []
        if cursor:
            query += " WHERE rowid < ?"
            params.append(int(cursor))
        query += " ORDER BY rowid DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._work_pool_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def get_work_pool(self, work_pool_id: str) -> dict[str, Any] | None:
        rows = self._query_rows(
            "SELECT rowid AS seq, id, name, type, paused, created_at, updated_at FROM work_pools WHERE id = ? LIMIT 1",
            [work_pool_id],
        )
        if not rows:
            return None
        return self._work_pool_row_to_dict(rows[0])

    def create_work_pool(self, name: str, pool_type: str = "process") -> dict[str, Any]:
        if pool_type != "process":
            raise ValueError("only process work pools are supported in MVP")
        existing = self._query_rows(
            "SELECT rowid AS seq, id, name, type, paused, created_at, updated_at FROM work_pools WHERE name = ? LIMIT 1",
            [name],
        )
        if existing:
            return self._work_pool_row_to_dict(existing[0])
        now = self._now()
        pool_id = str(uuid4())
        with self._lock:
            self._sqlite_conn.execute(
                "INSERT INTO work_pools(id,name,type,paused,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                [pool_id, name, pool_type, 0, now, now],
            )
        created = self.get_work_pool(pool_id)
        if created is None:
            raise RuntimeError("failed to create work pool")
        return created

    def patch_work_pool(
        self, work_pool_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        current = self.get_work_pool(work_pool_id)
        if current is None:
            raise ValueError("work pool not found")
        paused = current["paused"]
        if "paused" in patch:
            paused = bool(patch["paused"])
        now = self._now()
        with self._lock:
            self._sqlite_conn.execute(
                "UPDATE work_pools SET paused = ?, updated_at = ? WHERE id = ?",
                [1 if paused else 0, now, work_pool_id],
            )
        updated = self.get_work_pool(work_pool_id)
        if updated is None:
            raise ValueError("work pool not found")
        return updated

    def list_workers(
        self,
        work_pool_id: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> PageResult:
        query = "SELECT rowid AS seq, name, last_heartbeat, status, updated_at, work_pool_id FROM workers"
        params: list[Any] = []
        conditions: list[str] = []
        if work_pool_id:
            conditions.append("work_pool_id = ?")
            params.append(work_pool_id)
        if cursor:
            conditions.append("rowid < ?")
            params.append(int(cursor))
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY rowid DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._worker_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def _ensure_default_work_pool(self) -> None:
        now = self._now()
        with self._lock:
            self._sqlite_conn.execute(
                """
                INSERT OR IGNORE INTO work_pools(id,name,type,paused,created_at,updated_at)
                VALUES(?,?,?,?,?,?)
                """,
                [DEFAULT_WORK_POOL_ID, "default-process-pool", "process", 0, now, now],
            )
            self._sqlite_conn.execute(
                "UPDATE deployments SET work_pool_id = ? WHERE work_pool_id IS NULL",
                [DEFAULT_WORK_POOL_ID],
            )

    def claim_next_deployment_run(
        self, worker_name: str, lease_seconds: int = 30, work_pool_id: str | None = None
    ) -> dict[str, Any] | None:
        pool_id = work_pool_id or os.getenv("IRONFLOW_WORK_POOL", DEFAULT_WORK_POOL_ID)
        rust = self._rust_deployment_dispatch(
            "deployment_claim_next",
            {
                "worker_name": worker_name,
                "lease_seconds": lease_seconds,
                "work_pool_id": pool_id,
            },
        )
        if rust is not None:
            if rust.get("ok"):
                run = rust.get("run")
                return None if run is None else self._merge_resume_from_flow_run_id(run)
            err = rust.get("error") or {}
            raise RuntimeError(str(err.get("message", "deployment claim failed")))

        with self._lock:
            self._reclaim_expired_claims_python()
            now_dt = datetime.now(UTC)
            now = now_dt.isoformat()
            lease_until = (
                now_dt + timedelta(seconds=max(1, lease_seconds))
            ).isoformat()
            self._sqlite_conn.execute(
                """
                INSERT INTO workers(name,last_heartbeat,status,updated_at,work_pool_id)
                VALUES(?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    last_heartbeat=excluded.last_heartbeat,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    work_pool_id=excluded.work_pool_id
                """,
                [worker_name, now, "ONLINE", now, pool_id],
            )
            candidates = self._query_rows(
                """
                SELECT dr.id FROM deployment_runs dr
                INNER JOIN deployments d ON d.id = dr.deployment_id
                INNER JOIN work_pools wp ON wp.id = COALESCE(d.work_pool_id, 'default-process-pool') AND wp.paused = 0
                WHERE dr.status = 'SCHEDULED'
                AND COALESCE(d.work_pool_id, ?) = ?
                AND (
                  d.concurrency_limit IS NULL
                  OR (
                    SELECT COUNT(*) FROM deployment_runs x
                    WHERE x.deployment_id = dr.deployment_id
                    AND x.status IN ('CLAIMED','RUNNING')
                  ) < d.concurrency_limit
                )
                ORDER BY dr.created_at ASC
                LIMIT 1
                """,
                [DEFAULT_WORK_POOL_ID, pool_id],
            )
            if not candidates:
                return None
            candidate_id = candidates[0]["id"]
            self._sqlite_conn.execute(
                """
                UPDATE deployment_runs
                SET status = 'CLAIMED', worker_name = ?, lease_until = ?, updated_at = ?
                WHERE id = ? AND status = 'SCHEDULED'
                """,
                [worker_name, lease_until, now, candidate_id],
            )
            row = self._query_rows(
                """
                SELECT seq,id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
                       worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,parent_deployment_run_id,
                       resume_from_flow_run_id,
                       created_at,updated_at,started_at,finished_at
                FROM deployment_runs
                WHERE id = ? AND status = 'CLAIMED'
                LIMIT 1
                """,
                [candidate_id],
            )
            if not row:
                return None
            return self._deployment_run_row_to_dict(row[0])

    def claim_next_deployment_run_wait(
        self,
        worker_name: str,
        lease_seconds: int = 30,
        wait_ms: int = 500,
        work_pool_id: str | None = None,
    ) -> dict[str, Any] | None:
        pool_id = work_pool_id or os.getenv("IRONFLOW_WORK_POOL", DEFAULT_WORK_POOL_ID)
        rust = self._rust_deployment_dispatch(
            "deployment_claim_next_wait",
            {
                "worker_name": worker_name,
                "lease_seconds": lease_seconds,
                "wait_ms": wait_ms,
                "work_pool_id": pool_id,
            },
        )
        if rust is not None:
            if rust.get("ok"):
                run = rust.get("run")
                return None if run is None else run
            err = rust.get("error") or {}
            raise RuntimeError(str(err.get("message", "deployment claim wait failed")))

        deadline = time.monotonic() + max(wait_ms, 1) / 1000.0
        while time.monotonic() < deadline:
            c = self.claim_next_deployment_run(
                worker_name, lease_seconds, work_pool_id=pool_id
            )
            if c is not None:
                return c
            time.sleep(0.05)
        return None

    def mark_deployment_run_started(self, deployment_run_id: UUID) -> None:
        rust = self._rust_deployment_dispatch(
            "deployment_mark_run_started", {"deployment_run_id": str(deployment_run_id)}
        )
        if rust is not None and rust.get("ok"):
            return
        now = self._now()
        with self._lock:
            self._sqlite_conn.execute(
                """
                UPDATE deployment_runs
                SET status = 'RUNNING', started_at = ?, updated_at = ?
                WHERE id = ?
                """,
                [now, now, str(deployment_run_id)],
            )

    def attach_flow_run_to_deployment_run(
        self, deployment_run_id: UUID, flow_run_id: UUID
    ) -> None:
        rust = self._rust_deployment_dispatch(
            "deployment_attach_flow_run",
            {
                "deployment_run_id": str(deployment_run_id),
                "flow_run_id": str(flow_run_id),
            },
        )
        if rust is not None and rust.get("ok"):
            return
        now = self._now()
        with self._lock:
            self._sqlite_conn.execute(
                """
                UPDATE deployment_runs
                SET flow_run_id = ?, updated_at = ?
                WHERE id = ? AND (flow_run_id IS NULL OR flow_run_id = ?)
                """,
                [str(flow_run_id), now, str(deployment_run_id), str(flow_run_id)],
            )

    def mark_deployment_run_finished(
        self,
        deployment_run_id: UUID,
        status: str,
        flow_run_id: UUID | None = None,
        error: str | None = None,
    ) -> None:
        rust = self._rust_deployment_dispatch(
            "deployment_mark_run_finished",
            {
                "deployment_run_id": str(deployment_run_id),
                "status": status,
                "flow_run_id": str(flow_run_id) if flow_run_id else None,
                "error": error,
            },
        )
        if rust is not None and rust.get("ok"):
            return
        now = self._now()
        with self._lock:
            self._sqlite_conn.execute(
                """
                UPDATE deployment_runs
                SET status = ?, flow_run_id = ?, error = ?, finished_at = ?, updated_at = ?, lease_until = NULL
                WHERE id = ?
                """,
                [
                    status,
                    str(flow_run_id) if flow_run_id else None,
                    error,
                    now,
                    now,
                    str(deployment_run_id),
                ],
            )
