from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from ..persistence import DEFAULT_WORK_POOL_ID
from .types import (
    PageResult,
)


class DeploymentsMixin:
    def _count_exec_runs(self, deployment_id: str) -> int:
        rows = self._query_rows(
            """
            SELECT COUNT(*) AS c FROM deployment_runs
            WHERE deployment_id = ? AND status IN ('CLAIMED','RUNNING')
            """,
            [deployment_id],
        )
        return int(rows[0]["c"]) if rows else 0

    def _reclaim_expired_claims_python(self) -> int:
        now = self._now()
        cur = self._sqlite_conn.execute(
            """
            UPDATE deployment_runs
            SET status = 'SCHEDULED', worker_name = NULL, lease_until = NULL, updated_at = ?
            WHERE status = 'CLAIMED' AND lease_until IS NOT NULL AND lease_until < ?
            """,
            [now, now],
        )
        return int(cur.rowcount or 0)

    @staticmethod
    def _parse_rrule_until(value: str) -> datetime:
        raw = value.strip()
        if raw.endswith("Z") and "T" in raw and "-" not in raw:
            raw = raw[:-1]
        try:
            if "-" in raw:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
                    UTC
                )
            return datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        except ValueError as exc:
            raise ValueError(f"invalid RRule UNTIL: {exc}") from exc

    @classmethod
    def _next_rrule_occurrence(cls, expr: str, after: datetime | None = None) -> str:
        parts: dict[str, str] = {}
        for raw_part in expr.split(";"):
            part = raw_part.strip()
            if not part:
                continue
            key, sep, value = part.partition("=")
            if not sep:
                raise ValueError(f"invalid RRule component: {part}")
            normalized_key = key.strip().upper()
            if normalized_key not in {"FREQ", "INTERVAL", "UNTIL", "COUNT"}:
                raise ValueError(f"unsupported RRule component: {normalized_key}")
            parts[normalized_key] = value.strip()

        freq = parts.get("FREQ", "").upper()
        if freq not in {"MINUTELY", "HOURLY", "DAILY", "WEEKLY"}:
            raise ValueError(f"unsupported RRule FREQ: {freq or '<missing>'}")
        if "COUNT" in parts:
            raise ValueError(
                "RRule COUNT is not supported; use UNTIL or trigger fixed runs manually"
            )

        interval = int(parts.get("INTERVAL", "1"))
        if interval <= 0:
            raise ValueError("RRule INTERVAL must be positive")
        unit = {
            "MINUTELY": timedelta(minutes=interval),
            "HOURLY": timedelta(hours=interval),
            "DAILY": timedelta(days=interval),
            "WEEKLY": timedelta(weeks=interval),
        }[freq]
        base = after.astimezone(UTC) if after is not None else datetime.now(UTC)
        nxt = base + unit
        until_raw = parts.get("UNTIL")
        if until_raw:
            until = cls._parse_rrule_until(until_raw)
            if nxt > until:
                raise ValueError("RRule has no upcoming occurrence before UNTIL")
        return nxt.isoformat()

    def deployment_maintenance_tick(
        self, stale_after_seconds: int = 120
    ) -> dict[str, Any]:
        """Reclaim leases, fire due schedules, mark stale workers — prefers a single Rust FFI call."""
        rust = self._rust_deployment_dispatch(
            "deployment_maintenance", {"stale_after_seconds": stale_after_seconds}
        )
        if rust is not None and rust.get("ok"):
            summary = dict(rust.get("summary") or {})
            summary.setdefault("gates_promoted", 0)
            summary.update(self.retention_sweep())
            return summary
        with self._lock:
            reclaimed = self._reclaim_expired_claims_python()
            n_tick = self._tick_deployment_schedules_python()
            gates = self._tick_gate_tasks_python()
            now = self._now()
            cutoff = (
                datetime.now(UTC) - timedelta(seconds=max(1, stale_after_seconds))
            ).isoformat()
            cur = self._sqlite_conn.execute(
                "UPDATE workers SET status = 'OFFLINE', updated_at = ? WHERE status = 'ONLINE' AND last_heartbeat < ?",
                [now, cutoff],
            )
            reaped = int(cur.rowcount or 0)
        summary = {
            "reclaimed": reclaimed,
            "triggered": n_tick,
            "reaped": reaped,
            "gates_promoted": gates,
        }
        summary.update(self.retention_sweep())
        return summary

    def _tick_deployment_schedules_python(self) -> int:
        now = self._now()
        due = self._query_rows(
            """
            SELECT id, schedule_interval_seconds FROM deployments
            WHERE schedule_enabled = 1 AND paused = 0 AND deleted_at IS NULL
              AND schedule_interval_seconds IS NOT NULL AND schedule_interval_seconds > 0
              AND schedule_next_run_at IS NOT NULL AND schedule_next_run_at <= ?
            """,
            [now],
        )
        fired = 0
        for row in due:
            dep_id = UUID(row["id"])
            interval_sec = int(row["schedule_interval_seconds"])
            try:
                self.trigger_deployment_run(dep_id, parameters={}, idempotency_key=None)
            except ValueError:
                continue
            nxt = (datetime.now(UTC) + timedelta(seconds=interval_sec)).isoformat()
            ts = self._now()
            self._sqlite_conn.execute(
                "UPDATE deployments SET schedule_next_run_at = ?, updated_at = ? WHERE id = ?",
                [nxt, ts, str(dep_id)],
            )
            fired += 1
        due_rrule = self._query_rows(
            """
            SELECT id, schedule_rrule FROM deployments
            WHERE schedule_enabled = 1 AND paused = 0 AND deleted_at IS NULL
              AND schedule_rrule IS NOT NULL AND trim(schedule_rrule) != ''
              AND schedule_next_run_at IS NOT NULL AND schedule_next_run_at <= ?
            """,
            [now],
        )
        for row in due_rrule:
            dep_id = UUID(row["id"])
            rrule = str(row["schedule_rrule"])
            try:
                self.trigger_deployment_run(dep_id, parameters={}, idempotency_key=None)
                nxt = self._next_rrule_occurrence(rrule, datetime.now(UTC))
            except ValueError:
                continue
            ts = self._now()
            self._sqlite_conn.execute(
                "UPDATE deployments SET schedule_next_run_at = ?, updated_at = ? WHERE id = ?",
                [nxt, ts, str(dep_id)],
            )
            fired += 1
        return fired

    def worker_heartbeat(
        self, worker_name: str, work_pool_id: str | None = None
    ) -> None:
        pool_id = work_pool_id or os.getenv("IRONFLOW_WORK_POOL", DEFAULT_WORK_POOL_ID)
        rust = self._rust_deployment_dispatch(
            "deployment_worker_heartbeat",
            {"worker_name": worker_name, "work_pool_id": pool_id},
        )
        if rust is not None and rust.get("ok"):
            return
        now = self._now()
        with self._lock:
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

    def create_deployment(
        self,
        name: str,
        flow_name: str,
        entrypoint: str | None = None,
        path: str | None = None,
        default_parameters: dict[str, Any] | None = None,
        paused: bool = False,
        concurrency_limit: int | None = None,
        collision_strategy: str = "ENQUEUE",
        schedule_interval_seconds: int | None = None,
        schedule_cron: str | None = None,
        schedule_rrule: str | None = None,
        schedule_next_run_at: str | None = None,
        schedule_enabled: bool = False,
        work_pool_id: str | None = None,
        formerly: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        pool_id = work_pool_id or DEFAULT_WORK_POOL_ID
        catalog = self.ensure_flow(flow_name, formerly=formerly)
        body: dict[str, Any] = {
            "name": name,
            "flow_name": flow_name,
            "flow_id": catalog["id"],
            "entrypoint": entrypoint,
            "path": path,
            "default_parameters": default_parameters or {},
            "paused": paused,
            "concurrency_limit": concurrency_limit,
            "collision_strategy": collision_strategy,
            "schedule_interval_seconds": schedule_interval_seconds,
            "schedule_cron": schedule_cron,
            "schedule_rrule": schedule_rrule,
            "schedule_next_run_at": schedule_next_run_at,
            "schedule_enabled": schedule_enabled,
            "work_pool_id": pool_id,
        }
        rrule_requested = bool(schedule_rrule and str(schedule_rrule).strip())
        rust = (
            None
            if rrule_requested
            else self._rust_deployment_dispatch("deployment_create", body)
        )
        if rust is not None and rust.get("ok") and rust.get("deployment") is not None:
            deployment = self._deployment_from_rust_json(rust["deployment"])
            self._sqlite_conn.execute(
                "UPDATE deployments SET flow_id = ? WHERE id = ?",
                [catalog["id"], deployment["id"]],
            )
            deployment["flow_id"] = catalog["id"]
            return deployment
        if rust is not None and rust.get("ok") is False:
            err = rust.get("error") or {}
            raise ValueError(str(err.get("message", "deployment_create failed")))

        with self._lock:
            existing = self._query_rows(
                """
                SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                       concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,
                       schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at
                FROM deployments
                WHERE name = ? AND deleted_at IS NULL
                LIMIT 1
                """,
                [name],
            )
            if existing:
                return self._deployment_row_to_dict(existing[0])

            si = schedule_interval_seconds
            sc = schedule_cron
            sr = schedule_rrule
            if sr and str(sr).strip():
                si = None
                sc = None
            elif sc and str(sc).strip():
                si = None
                sr = None
            elif si is not None and si > 0:
                sc = None
                sr = None

            sched_next = schedule_next_run_at
            if schedule_enabled and si and si > 0 and sched_next is None:
                sched_next = self._now()
            if schedule_enabled and sr and str(sr).strip() and sched_next is None:
                sched_next = self._next_rrule_occurrence(str(sr), datetime.now(UTC))
            if schedule_enabled and sc and str(sc).strip() and sched_next is None:
                raise ValueError(
                    "Cron schedules require the Rust engine (bind_db) to compute the first schedule_next_run_at, "
                    "or pass schedule_next_run_at explicitly."
                )

            now = self._now()
            deployment_id = str(uuid4())
            self._sqlite_conn.execute(
                """
                INSERT INTO deployments
                (id,name,flow_name,entrypoint,path,default_parameters,paused,
                 concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,
                 schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at,flow_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    deployment_id,
                    name,
                    flow_name,
                    entrypoint,
                    path,
                    json.dumps(default_parameters or {}),
                    1 if paused else 0,
                    concurrency_limit,
                    collision_strategy,
                    si,
                    sc,
                    sr,
                    sched_next,
                    1 if schedule_enabled else 0,
                    pool_id,
                    now,
                    now,
                    catalog["id"],
                ],
            )
            row = self._query_rows(
                """
                SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                       concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,
                       schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at,flow_id
                FROM deployments
                WHERE id = ?
                LIMIT 1
                """,
                [deployment_id],
            )[0]
            return self._deployment_row_to_dict(row)

    def update_deployment(
        self, deployment_id: UUID, patch: dict[str, Any]
    ) -> dict[str, Any]:
        body = dict(patch)
        body["deployment_id"] = str(deployment_id)
        rrule_requested = bool(
            patch.get("schedule_rrule") and str(patch.get("schedule_rrule")).strip()
        )
        rust = (
            None
            if rrule_requested
            else self._rust_deployment_dispatch("deployment_update", body)
        )
        if rust is not None and rust.get("ok") and rust.get("deployment") is not None:
            deployment = self._deployment_from_rust_json(rust["deployment"])
            return deployment
        if rust is not None and rust.get("ok") is False:
            err = rust.get("error") or {}
            msg = str(err.get("message", "deployment_update failed"))
            if err.get("code") == "not_found":
                raise ValueError("deployment not found")
            raise ValueError(msg)

        return self._update_deployment_python(deployment_id, patch)

    def _update_deployment_python(
        self, deployment_id: UUID, patch: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            dep = self.get_deployment(deployment_id)
            if dep is None:
                raise ValueError("deployment not found")
            entrypoint = dep.get("entrypoint")
            path = dep.get("path")
            default_parameters = dict(dep.get("default_parameters") or {})
            paused = bool(dep.get("paused"))
            concurrency_limit = dep.get("concurrency_limit")
            collision_strategy = str(dep.get("collision_strategy") or "ENQUEUE")
            schedule_interval_seconds = dep.get("schedule_interval_seconds")
            schedule_cron = dep.get("schedule_cron")
            schedule_rrule = dep.get("schedule_rrule")
            schedule_next_run_at = dep.get("schedule_next_run_at")
            schedule_enabled = bool(dep.get("schedule_enabled"))
            work_pool_id = dep.get("work_pool_id") or DEFAULT_WORK_POOL_ID

            if "entrypoint" in patch:
                v = patch["entrypoint"]
                entrypoint = None if v is None else str(v)
            if "path" in patch:
                v = patch["path"]
                path = None if v is None else str(v)
            if (
                "default_parameters" in patch
                and patch["default_parameters"] is not None
            ):
                default_parameters = dict(patch["default_parameters"])
            if "paused" in patch:
                paused = bool(patch["paused"])
            if "concurrency_limit" in patch:
                concurrency_limit = patch["concurrency_limit"]
            if (
                "collision_strategy" in patch
                and patch["collision_strategy"] is not None
            ):
                collision_strategy = str(patch["collision_strategy"])
            if "schedule_interval_seconds" in patch:
                schedule_interval_seconds = patch["schedule_interval_seconds"]
            if "schedule_cron" in patch:
                schedule_cron = patch["schedule_cron"]
            if "schedule_rrule" in patch:
                schedule_rrule = patch["schedule_rrule"]
            if "schedule_next_run_at" in patch:
                schedule_next_run_at = patch["schedule_next_run_at"]
            if "schedule_enabled" in patch:
                schedule_enabled = bool(patch["schedule_enabled"])
            if "work_pool_id" in patch and patch["work_pool_id"] is not None:
                work_pool_id = str(patch["work_pool_id"])

            if schedule_rrule and str(schedule_rrule).strip():
                schedule_interval_seconds = None
                schedule_cron = None
            elif schedule_cron and str(schedule_cron).strip():
                schedule_interval_seconds = None
                schedule_rrule = None
            elif (
                schedule_interval_seconds is not None
                and int(schedule_interval_seconds) > 0
            ):
                schedule_cron = None
                schedule_rrule = None

            if (
                schedule_enabled
                and schedule_interval_seconds
                and int(schedule_interval_seconds) > 0
                and not schedule_next_run_at
            ):
                schedule_next_run_at = self._now()
            if (
                schedule_enabled
                and schedule_rrule
                and str(schedule_rrule).strip()
                and not schedule_next_run_at
            ):
                schedule_next_run_at = self._next_rrule_occurrence(
                    str(schedule_rrule), datetime.now(UTC)
                )
            if (
                schedule_enabled
                and schedule_cron
                and str(schedule_cron).strip()
                and not schedule_next_run_at
            ):
                raise ValueError(
                    "Cron schedules require schedule_next_run_at when the Rust kernel is unavailable, "
                    "or use the native engine with bind_db."
                )

            ts = self._now()
            self._sqlite_conn.execute(
                """
                UPDATE deployments SET
                  entrypoint = ?, path = ?, default_parameters = ?, paused = ?,
                  concurrency_limit = ?, collision_strategy = ?,
                  schedule_interval_seconds = ?, schedule_cron = ?, schedule_rrule = ?, schedule_next_run_at = ?,
                  schedule_enabled = ?, work_pool_id = ?, updated_at = ?
                WHERE id = ?
                """,
                [
                    entrypoint,
                    path,
                    json.dumps(default_parameters),
                    1 if paused else 0,
                    concurrency_limit,
                    collision_strategy,
                    schedule_interval_seconds,
                    schedule_cron,
                    schedule_rrule,
                    schedule_next_run_at,
                    1 if schedule_enabled else 0,
                    work_pool_id,
                    ts,
                    str(deployment_id),
                ],
            )
            row = self._query_rows(
                """
                SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                       concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,
                       schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at
                FROM deployments WHERE id = ?
                LIMIT 1
                """,
                [str(deployment_id)],
            )[0]
            return self._deployment_row_to_dict(row)

    def list_deployments(
        self, limit: int = 200, cursor: str | None = None
    ) -> PageResult:
        query = (
            "SELECT seq,id,name,flow_name,entrypoint,path,default_parameters,paused,"
            " concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,"
            " schedule_next_run_at,schedule_enabled,created_at,updated_at,flow_id "
            "FROM deployments WHERE deleted_at IS NULL"
        )
        params: list[Any] = []
        if cursor:
            query += " AND seq < ?"
            params.append(int(cursor))
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._query_rows(query, params)
        items = [self._deployment_row_to_dict(r) for r in rows]
        next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
        return PageResult(items=items, next_cursor=next_cursor)

    def get_deployment(self, deployment_id: UUID) -> dict[str, Any] | None:
        rows = self._query_rows(
            """
            SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                   concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,
                   schedule_next_run_at,schedule_enabled,created_at,updated_at
            FROM deployments
            WHERE id = ? AND deleted_at IS NULL
            LIMIT 1
            """,
            [str(deployment_id)],
        )
        if not rows:
            return None
        return self._deployment_row_to_dict(rows[0])

    def get_deployment_by_name(self, name: str) -> dict[str, Any] | None:
        rows = self._query_rows(
            """
            SELECT id,name,flow_name,entrypoint,path,default_parameters,paused,
                   concurrency_limit,collision_strategy,schedule_interval_seconds,schedule_cron,schedule_rrule,
                   schedule_next_run_at,schedule_enabled,work_pool_id,created_at,updated_at
            FROM deployments
            WHERE name = ? AND deleted_at IS NULL
            LIMIT 1
            """,
            [name],
        )
        if not rows:
            return None
        return self._deployment_row_to_dict(rows[0])
