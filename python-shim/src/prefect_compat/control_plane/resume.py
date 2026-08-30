from __future__ import annotations

from typing import Any
from uuid import UUID

from ..result_codec import ResultEncodeError, decode_task_result, encode_task_result


class ResumeMixin:
    def prepare_resume(self, from_flow_run_id: UUID) -> None:
        """Queue resume lineage for the next ``create_flow_run`` (in-process tests / helpers)."""
        with self._lock:
            self._ensure_resume_schema()
            if from_flow_run_id not in self._flows:
                rows = self._query_rows(
                    "SELECT id FROM flow_runs WHERE id = ? LIMIT 1",
                    [str(from_flow_run_id)],
                )
                if not rows:
                    raise ValueError("resume_from flow run not found")
            self._pending_resume_from = from_flow_run_id
            self._resume_lookups_enabled = True

    def consume_pending_resume(self) -> UUID | None:
        with self._lock:
            pending = self._pending_resume_from
            self._pending_resume_from = None
            return pending

    def _lineage_id_for_flow(self, flow_run_id: UUID) -> UUID:
        rec = self._flows.get(flow_run_id)
        if rec is not None:
            return rec.resume_lineage_id or rec.run_id
        rows = self._query_rows(
            "SELECT resume_lineage_id FROM flow_runs WHERE id = ? LIMIT 1",
            [str(flow_run_id)],
        )
        if rows and rows[0]["resume_lineage_id"]:
            return UUID(str(rows[0]["resume_lineage_id"]))
        return flow_run_id

    def effective_resume_lineage_id(self, flow_run_id: UUID) -> UUID | None:
        rec = self._flows.get(flow_run_id)
        if rec is None:
            return None
        if rec.resume_lineage_id is not None:
            return rec.resume_lineage_id
        # Fresh runs use their own id as lineage root when storing results.
        return rec.run_id

    def _ensure_resume_schema(self) -> None:
        """Ensure resume columns / cache table exist (SQLite + Postgres)."""
        if self._resume_schema_ready:
            return
        if getattr(self._store, "backend_kind", "sqlite") == "postgres":
            # Canonical DDL + IF NOT EXISTS upgrades live on PostgresStore.
            self._store.ensure_schema()
            self._task_result_cache_ready = True
            self._resume_schema_ready = True
            return
        flow_cols = {
            c["name"]
            for c in self._sqlite_conn.execute(
                "PRAGMA table_info(flow_runs)"
            ).fetchall()
        }
        if "resume_from_flow_run_id" not in flow_cols:
            self._sqlite_conn.execute(
                "ALTER TABLE flow_runs ADD COLUMN resume_from_flow_run_id TEXT"
            )
        if "resume_lineage_id" not in flow_cols:
            self._sqlite_conn.execute(
                "ALTER TABLE flow_runs ADD COLUMN resume_lineage_id TEXT"
            )
        if "parameters_fingerprint" not in flow_cols:
            self._sqlite_conn.execute(
                "ALTER TABLE flow_runs ADD COLUMN parameters_fingerprint TEXT"
            )
        dep_run_cols = {
            c["name"]
            for c in self._sqlite_conn.execute(
                "PRAGMA table_info(deployment_runs)"
            ).fetchall()
        }
        if "resume_from_flow_run_id" not in dep_run_cols:
            self._sqlite_conn.execute(
                "ALTER TABLE deployment_runs ADD COLUMN resume_from_flow_run_id TEXT"
            )
        self._ensure_task_result_cache_table()
        self._resume_schema_ready = True

    def _ensure_task_result_cache_table(self) -> None:
        if self._task_result_cache_ready:
            return
        if getattr(self._store, "backend_kind", "sqlite") == "postgres":
            self._store.ensure_schema()
            self._task_result_cache_ready = True
            return
        self._sqlite_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_result_cache (
                lineage_id TEXT NOT NULL,
                planned_node_id TEXT NOT NULL,
                map_index INTEGER NOT NULL,
                task_name TEXT NOT NULL,
                is_none_result INTEGER NOT NULL,
                has_payload INTEGER NOT NULL,
                payload_json TEXT,
                input_fingerprint TEXT NOT NULL DEFAULT '',
                source_flow_run_id TEXT NOT NULL,
                source_task_run_id TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (lineage_id, planned_node_id, map_index)
            )
            """
        )
        cache_cols = {
            c["name"]
            for c in self._sqlite_conn.execute(
                "PRAGMA table_info(task_result_cache)"
            ).fetchall()
        }
        if "input_fingerprint" not in cache_cols:
            self._sqlite_conn.execute(
                "ALTER TABLE task_result_cache ADD COLUMN input_fingerprint "
                "TEXT NOT NULL DEFAULT ''"
            )
        self._task_result_cache_ready = True

    def lookup_resumed_task_result(
        self,
        flow_run_id: UUID,
        planned_node_id: str | None,
        *,
        map_index: int | None = None,
        persist_result: bool = False,
        input_fingerprint: str | None = None,
    ) -> tuple[bool, Any]:
        """Return ``(hit, value)`` for a DAG node on a resume run."""
        if not self._resume_lookups_enabled:
            return False, None
        if not planned_node_id or not input_fingerprint:
            return False, None
        rec = self._flows.get(flow_run_id)
        if (
            rec is None
            or rec.resume_from_flow_run_id is None
            or not rec.resume_skips_enabled
        ):
            return False, None
        lineage_id = rec.resume_lineage_id or rec.run_id
        map_key = -1 if map_index is None else int(map_index)
        with self._lock:
            self._ensure_resume_schema()
        rows = self._query_rows(
            """
            SELECT is_none_result, has_payload, payload_json, input_fingerprint
            FROM task_result_cache
            WHERE lineage_id = ? AND planned_node_id = ? AND map_index = ?
            LIMIT 1
            """,
            [str(lineage_id), planned_node_id, map_key],
        )
        if not rows:
            return False, None
        row = rows[0]
        if str(row["input_fingerprint"] or "") != input_fingerprint:
            return False, None
        if int(row["is_none_result"] or 0) == 1:
            return True, None
        if int(row["has_payload"] or 0) != 1:
            return False, None
        if not persist_result:
            return False, None
        raw = row["payload_json"]
        if raw is None:
            return False, None
        try:
            return True, decode_task_result(str(raw))
        except Exception:
            return False, None

    def store_task_result_for_resume(
        self,
        flow_run_id: UUID,
        task_run_id: UUID,
        task_name: str,
        planned_node_id: str | None,
        value: Any,
        *,
        persist_result: bool = False,
        map_index: int | None = None,
        input_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """Persist a completion marker / optional JSON payload for DAG resume.

        Returns artifact summary fields (``result`` / ``persisted``) when applicable.
        Cache rows are only written when ``input_fingerprint`` is known (JSON-safe inputs).
        """
        summary_extra: dict[str, Any] = {}
        if not planned_node_id:
            return summary_extra
        is_none = value is None
        if not is_none and not persist_result:
            # Non-persisted value-producing tasks recompute on resume — skip store write.
            return summary_extra
        payload_json: str | None = None
        has_payload = False
        if is_none:
            summary_extra["result"] = None
            summary_extra["persisted"] = True
        else:
            try:
                payload_json = encode_task_result(value)
                has_payload = True
                summary_extra["result"] = decode_task_result(payload_json)
                summary_extra["persisted"] = True
            except ResultEncodeError:
                summary_extra["persisted"] = False
                return summary_extra
        if not input_fingerprint:
            # Unfingerprintable inputs: surface artifact summary but never resume-skip.
            return summary_extra
        lineage_id = self.effective_resume_lineage_id(flow_run_id)
        if lineage_id is None:
            return summary_extra
        map_key = -1 if map_index is None else int(map_index)
        now = self._now()
        with self._lock:
            self._ensure_resume_schema()
            self._sqlite_conn.execute(
                """
                INSERT INTO task_result_cache(
                    lineage_id, planned_node_id, map_index, task_name,
                    is_none_result, has_payload, payload_json, input_fingerprint,
                    source_flow_run_id, source_task_run_id, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(lineage_id, planned_node_id, map_index) DO UPDATE SET
                    task_name=excluded.task_name,
                    is_none_result=excluded.is_none_result,
                    has_payload=excluded.has_payload,
                    payload_json=excluded.payload_json,
                    input_fingerprint=excluded.input_fingerprint,
                    source_flow_run_id=excluded.source_flow_run_id,
                    source_task_run_id=excluded.source_task_run_id,
                    updated_at=excluded.updated_at
                """,
                [
                    str(lineage_id),
                    planned_node_id,
                    map_key,
                    task_name,
                    1 if is_none else 0,
                    1 if has_payload else 0,
                    payload_json,
                    input_fingerprint,
                    str(flow_run_id),
                    str(task_run_id),
                    now,
                ],
            )
            # Fresh runs: keep lineage in memory as run_id; SQL update only for non-identity lineages.
            flow = self._flows.get(flow_run_id)
            if flow is not None and flow.resume_lineage_id is None:
                flow.resume_lineage_id = lineage_id
                if lineage_id != flow_run_id:
                    self._sqlite_conn.execute(
                        "UPDATE flow_runs SET resume_lineage_id = ? WHERE id = ?",
                        [str(lineage_id), str(flow_run_id)],
                    )
        return summary_extra

    def _merge_resume_from_flow_run_id(
        self, run: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Attach SQLite ``resume_from_flow_run_id`` when Rust ops omit the column."""
        if run is None or run.get("resume_from_flow_run_id"):
            return run
        run_id = run.get("id")
        if not run_id:
            return run
        with self._lock:
            self._ensure_resume_schema()
        rows = self._query_rows(
            "SELECT resume_from_flow_run_id FROM deployment_runs WHERE id = ? LIMIT 1",
            [str(run_id)],
        )
        if not rows:
            return run
        merged = dict(run)
        merged["resume_from_flow_run_id"] = rows[0]["resume_from_flow_run_id"]
        return merged
