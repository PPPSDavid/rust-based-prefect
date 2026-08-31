from __future__ import annotations

import logging
from typing import Any
from uuid import UUID


class GclMixin:
    def upsert_concurrency_limit(
        self,
        name: str,
        limit: int,
        *,
        slot_decay_per_second: float | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        from .. import concurrency_store as gcl

        body: dict[str, Any] = {
            "name": name,
            "limit": limit,
            "active": active,
        }
        if slot_decay_per_second is not None:
            body["slot_decay_per_second"] = slot_decay_per_second
        rust = self._gcl_dispatch("gcl_upsert", body)
        if rust is not None and rust.get("ok") and "limit" in rust:
            return rust["limit"]
        with self._lock:
            return gcl.upsert_limit(self._sqlite_conn, body)

    def delete_concurrency_limit(self, name: str) -> dict[str, Any]:
        from .. import concurrency_store as gcl

        rust = self._gcl_dispatch("gcl_delete", {"name": name})
        if rust is not None and "deleted" in rust:
            return rust
        with self._lock:
            return gcl.delete_limit(self._sqlite_conn, name)

    def get_concurrency_limit(self, name: str) -> dict[str, Any] | None:
        from .. import concurrency_store as gcl

        rust = self._gcl_dispatch("gcl_get", {"name": name})
        if rust is not None and "limit" in rust:
            lim = rust["limit"]
            return lim if lim is not None else None
        with self._lock:
            return gcl.get_limit(self._sqlite_conn, name).get("limit")

    def list_concurrency_limits(self) -> list[dict[str, Any]]:
        from .. import concurrency_store as gcl

        rust = self._gcl_dispatch("gcl_list", {})
        if rust is not None and "limits" in rust:
            return list(rust["limits"] or [])
        with self._lock:
            return list(gcl.list_limits(self._sqlite_conn).get("limits") or [])

    def acquire_concurrency_slots(
        self,
        names: str | list[str],
        *,
        occupy: int = 1,
        mode: str = "concurrency",
        strict: bool = False,
        lease_duration: float = 300.0,
        holder_type: str | None = None,
        holder_id: str | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        from .. import concurrency_store as gcl

        body: dict[str, Any] = {
            "names": names,
            "occupy": occupy,
            "mode": mode,
            "strict": strict,
            "lease_duration": lease_duration,
        }
        if holder_type is not None:
            body["holder_type"] = holder_type
        if holder_id is not None:
            body["holder_id"] = holder_id
        if now is not None:
            body["now"] = now
        rust = self._gcl_dispatch("gcl_acquire", body)
        if rust is not None and "status" in rust:
            return rust
        with self._lock:
            return gcl.acquire(self._sqlite_conn, body)

    def release_concurrency_slots(
        self,
        lease_ids: str | list[str],
        *,
        now: str | None = None,
    ) -> dict[str, Any]:
        from .. import concurrency_store as gcl

        body: dict[str, Any] = {"lease_ids": lease_ids}
        if now is not None:
            body["now"] = now
        rust = self._gcl_dispatch("gcl_release", body)
        if rust is not None and "released" in rust:
            return rust
        with self._lock:
            return gcl.release(self._sqlite_conn, body)

    def release_concurrency_slots_for_holders(
        self,
        holder_ids: str | list[str],
        *,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Release GCL leases owned by task/flow run ids (cancel/terminate)."""
        from .. import concurrency_store as gcl

        body: dict[str, Any] = {"holder_ids": holder_ids}
        if now is not None:
            body["now"] = now
        rust = self._gcl_dispatch("gcl_release_by_holders", body)
        if rust is not None and "released" in rust:
            return rust
        with self._lock:
            return gcl.release_by_holders(self._sqlite_conn, body)

    def _release_gcl_holders_for_flow(self, flow_run_id: UUID) -> None:
        holder_ids = [str(flow_run_id)]
        rows = self._query_rows(
            "SELECT id FROM task_runs WHERE flow_run_id = ?",
            [str(flow_run_id)],
        )
        holder_ids.extend(str(row["id"]) for row in rows)
        with self._lock:
            holder_ids.extend(
                str(tid)
                for tid, task in self._tasks.items()
                if str(task.flow_run_id) == str(flow_run_id)
            )
        unique_ids = list(dict.fromkeys(holder_ids))
        try:
            self.release_concurrency_slots_for_holders(unique_ids)
        except Exception:
            logging.getLogger(__name__).exception(
                "GCL release-by-holder failed for flow %s", flow_run_id
            )

    def renew_concurrency_slots(
        self,
        lease_ids: str | list[str],
        *,
        lease_duration: float = 300.0,
        now: str | None = None,
    ) -> dict[str, Any]:
        from .. import concurrency_store as gcl

        body: dict[str, Any] = {
            "lease_ids": lease_ids,
            "lease_duration": lease_duration,
        }
        if now is not None:
            body["now"] = now
        rust = self._gcl_dispatch("gcl_renew", body)
        if rust is not None and "renewed" in rust:
            return rust
        with self._lock:
            return gcl.renew(self._sqlite_conn, body)

    def reclaim_concurrency_leases(self, *, now: str | None = None) -> int:
        from .. import concurrency_store as gcl

        body: dict[str, Any] = {}
        if now is not None:
            body["now"] = now
        rust = self._gcl_dispatch("gcl_reclaim_expired", body)
        if rust is not None and "reclaimed" in rust:
            return int(rust["reclaimed"])
        with self._lock:
            return gcl.reclaim_expired(self._sqlite_conn, now)
