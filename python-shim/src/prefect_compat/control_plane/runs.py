from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

from .types import (
    SUBFLOW_MAX_DEPTH,
    FlowRunRecord,
    FlowRunSchedulingHeld,
    RunState,
    SetStateResult,
    TaskRunRecord,
)
from .types import (
    legacy_is_valid_transition as _legacy_is_valid_transition,
)


class RunsMixin:
    def create_flow_run(
        self,
        name: str,
        *,
        parent_flow_run_id: UUID | None = None,
        parent_task_run_id: UUID | None = None,
        execution_mode: str | None = None,
        resume_from_flow_run_id: UUID | None = None,
        parameters_fingerprint: str | None = None,
    ) -> FlowRunRecord:
        run_id = uuid4()
        root_flow_run_id: UUID | None = run_id
        depth = 0
        if parent_flow_run_id is not None:
            parent = self.get_flow(parent_flow_run_id)
            root_flow_run_id = parent.root_flow_run_id or parent_flow_run_id
            depth = parent.depth + 1
            if depth > SUBFLOW_MAX_DEPTH:
                raise ValueError(f"subflow depth exceeds maximum ({SUBFLOW_MAX_DEPTH})")
        resume_lineage_id: UUID | None = None
        resume_skips_enabled = False
        if resume_from_flow_run_id is not None:
            with self._lock:
                self._ensure_resume_schema()
            prior = self._flows.get(resume_from_flow_run_id)
            prior_fp: str | None = None
            if prior is None:
                # Durable path: prior may only exist in SQLite after process restart.
                prior_row = self._query_rows(
                    "SELECT id, parameters_fingerprint FROM flow_runs WHERE id = ? LIMIT 1",
                    [str(resume_from_flow_run_id)],
                )
                if not prior_row:
                    raise ValueError("resume_from flow run not found")
                resume_lineage_id = self._lineage_id_for_flow(resume_from_flow_run_id)
                raw_fp = prior_row[0]["parameters_fingerprint"]
                prior_fp = str(raw_fp) if raw_fp else None
            else:
                resume_lineage_id = prior.resume_lineage_id or prior.run_id
                prior_fp = prior.parameters_fingerprint
            # Case 6: only skip when flow/deployment params match the prior attempt.
            resume_skips_enabled = (
                prior_fp is not None
                and parameters_fingerprint is not None
                and prior_fp == parameters_fingerprint
            )
            if resume_skips_enabled:
                self._resume_lookups_enabled = True
        record = FlowRunRecord(
            run_id=run_id,
            name=name,
            state=RunState.SCHEDULED,
            version=0,
            parent_flow_run_id=parent_flow_run_id,
            parent_task_run_id=parent_task_run_id,
            root_flow_run_id=root_flow_run_id,
            execution_mode=execution_mode,
            depth=depth,
            resume_from_flow_run_id=resume_from_flow_run_id,
            resume_lineage_id=resume_lineage_id,
            parameters_fingerprint=parameters_fingerprint,
            resume_skips_enabled=resume_skips_enabled,
        )
        with self._lock:
            persisted_by_rust = False
            if self._rust_fsm_active() and self._rust_native_persistence:
                out = self._rust_fsm_call(
                    "create_flow_run_persist",
                    {
                        **(
                            {}
                            if self._rust_db_bound
                            else {"db_path": str(self._sqlite_path)}
                        ),
                        "run": {
                            "id": str(record.run_id),
                            "name": record.name,
                            "state": record.state.value,
                            "version": int(record.version),
                            "parent_flow_run_id": str(parent_flow_run_id)
                            if parent_flow_run_id
                            else None,
                            "parent_task_run_id": str(parent_task_run_id)
                            if parent_task_run_id
                            else None,
                            "root_flow_run_id": str(root_flow_run_id)
                            if root_flow_run_id
                            else None,
                            "execution_mode": execution_mode,
                            "depth": depth,
                        },
                    },
                )
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if self._is_unknown_op_error(err, "create_flow_run_persist"):
                        self._rust_native_persistence = False
                    else:
                        self._raise_from_rust_fsm_error(err)
                else:
                    persisted_by_rust = True
            self._flows[record.run_id] = record
            self._latest_flow_run_id = record.run_id
            if not persisted_by_rust:
                self._insert_flow_row(record)
            else:
                # Rust create_flow_run_persist omits resume/param-fingerprint columns.
                if (
                    record.resume_from_flow_run_id is not None
                    or record.resume_lineage_id is not None
                    or record.parameters_fingerprint is not None
                ):
                    self._ensure_resume_schema()
                    self._sqlite_conn.execute(
                        "UPDATE flow_runs SET resume_from_flow_run_id = ?, "
                        "resume_lineage_id = ?, parameters_fingerprint = ? "
                        "WHERE id = ?",
                        [
                            str(record.resume_from_flow_run_id)
                            if record.resume_from_flow_run_id
                            else None,
                            str(record.resume_lineage_id)
                            if record.resume_lineage_id
                            else None,
                            record.parameters_fingerprint,
                            str(record.run_id),
                        ],
                    )
            self._persist_record(
                {
                    "record_type": "flow_create",
                    "run_id": str(record.run_id),
                    "name": record.name,
                    "state": record.state.value,
                    "version": record.version,
                    "parent_flow_run_id": str(parent_flow_run_id)
                    if parent_flow_run_id
                    else None,
                    "parent_task_run_id": str(parent_task_run_id)
                    if parent_task_run_id
                    else None,
                    "root_flow_run_id": str(root_flow_run_id)
                    if root_flow_run_id
                    else None,
                    "execution_mode": execution_mode,
                    "depth": depth,
                }
            )
            if (not self._rust_native_persistence) and self._rust_fsm_active():
                self._rust_register_flow(record)
        return record

    def create_task_run(
        self,
        flow_run_id: UUID,
        task_name: str,
        planned_node_id: str | None = None,
        *,
        kind: str = "task",
        child_flow_run_id: UUID | None = None,
        child_deployment_run_id: UUID | None = None,
        gate_open_at: str | None = None,
        tags: Sequence[str] | None = None,
        contribute_to_flow_state: bool = True,
    ) -> TaskRunRecord:
        tag_tuple = tuple(str(t) for t in (tags or ()))
        task = TaskRunRecord(
            task_run_id=uuid4(),
            flow_run_id=flow_run_id,
            task_name=task_name,
            planned_node_id=planned_node_id,
            state=RunState.SCHEDULED,
            version=0,
            kind=kind,
            child_flow_run_id=child_flow_run_id,
            child_deployment_run_id=child_deployment_run_id,
            gate_open_at=gate_open_at,
            tags=tag_tuple,
            contribute_to_flow_state=contribute_to_flow_state,
        )
        with self._lock:
            if self._is_scheduling_held_unlocked(flow_run_id):
                raise FlowRunSchedulingHeld(
                    f"flow run {flow_run_id} is paused; new tasks cannot start until resume"
                )
            self._tasks[task.task_run_id] = task
            persisted_by_rust = False
            if self._rust_fsm_active() and self._rust_native_persistence:
                out = self._rust_fsm_call(
                    "create_task_run_persist",
                    {
                        **(
                            {}
                            if self._rust_db_bound
                            else {"db_path": str(self._sqlite_path)}
                        ),
                        "planned_node_id": task.planned_node_id,
                        "kind": task.kind,
                        "child_flow_run_id": str(child_flow_run_id)
                        if child_flow_run_id
                        else None,
                        "child_deployment_run_id": str(child_deployment_run_id)
                        if child_deployment_run_id
                        else None,
                        "gate_open_at": gate_open_at,
                        "contribute_to_flow_state": task.contribute_to_flow_state,
                        "task": {
                            "id": str(task.task_run_id),
                            "flow_run_id": str(task.flow_run_id),
                            "task_key": task.task_name,
                            "state": task.state.value,
                            "version": int(task.version),
                        },
                    },
                )
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if self._is_unknown_op_error(err, "create_task_run_persist"):
                        self._rust_native_persistence = False
                    else:
                        self._raise_from_rust_fsm_error(err)
                else:
                    persisted_by_rust = True
            if not persisted_by_rust:
                self._insert_task_row(task)
            self._persist_record(
                {
                    "record_type": "task_create",
                    "task_run_id": str(task.task_run_id),
                    "flow_run_id": str(task.flow_run_id),
                    "task_name": task.task_name,
                    "planned_node_id": task.planned_node_id,
                    "state": task.state.value,
                    "version": task.version,
                    "tags": list(task.tags),
                    "contribute_to_flow_state": task.contribute_to_flow_state,
                }
            )
            if (not self._rust_native_persistence) and self._rust_fsm_active():
                self._rust_register_task(task)
        return task

    def save_flow_manifest(
        self,
        run_id: UUID,
        manifest: dict[str, Any] | None,
        forecast: dict[str, Any] | None,
        warnings: list[str] | None,
        fallback_required: bool,
        source: str,
    ) -> None:
        manifest = manifest or {}
        nodes = manifest.get("nodes", [])
        task_to_ids: dict[str, list[str]] = {}
        for node in nodes:
            tn = node.get("task_name")
            nid = node.get("node_id")
            if tn and nid is not None:
                task_to_ids.setdefault(str(tn), []).append(str(nid))
        with self._lock:
            self._manifest_by_task[run_id] = task_to_ids
            manifest_json = json.dumps(manifest)
            forecast_json = json.dumps(forecast or {})
            warnings_json = json.dumps(warnings or [])
            persisted_by_rust = False
            if self._rust_fsm_active() and self._rust_native_persistence:
                out = self._rust_fsm_call(
                    "save_flow_manifest_persist",
                    {
                        **(
                            {}
                            if self._rust_db_bound
                            else {"db_path": str(self._sqlite_path)}
                        ),
                        "flow_run_id": str(run_id),
                        "manifest_json": manifest_json,
                        "forecast_json": forecast_json,
                        "warnings_json": warnings_json,
                        "fallback_required": bool(fallback_required),
                        "source": source,
                    },
                )
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if self._is_unknown_op_error(err, "save_flow_manifest_persist"):
                        self._rust_native_persistence = False
                    else:
                        self._raise_from_rust_fsm_error(err)
                else:
                    persisted_by_rust = True
            if not persisted_by_rust:
                self._sqlite_conn.execute(
                    """
                    INSERT OR REPLACE INTO dag_manifests
                    (flow_run_id, manifest_json, forecast_json, warnings_json, fallback_required, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(run_id),
                        manifest_json,
                        forecast_json,
                        warnings_json,
                        1 if fallback_required else 0,
                        source,
                        self._now(),
                    ],
                )

    def next_planned_node_id(self, flow_run_id: UUID, task_name: str) -> str | None:
        with self._lock:
            reserved = self._reserved_planned_ids.setdefault(flow_run_id, set())
            task_run_used = {
                str(task.planned_node_id)
                for task in self._tasks.values()
                if task.flow_run_id == flow_run_id and task.planned_node_id
            }
            taken = reserved | task_run_used

            by_task = self._manifest_by_task.get(flow_run_id)
            if by_task is not None:
                for node_id in by_task.get(task_name, []):
                    if node_id not in taken:
                        reserved.add(node_id)
                        return node_id
            else:
                manifest_id = self._next_planned_from_sql_unlocked(
                    flow_run_id, task_name
                )
                if manifest_id is not None and manifest_id not in taken:
                    reserved.add(manifest_id)
                    return manifest_id

            index = 0
            while True:
                candidate = f"dyn_{task_name}_{index}"
                if candidate not in taken:
                    reserved.add(candidate)
                    return candidate
                index += 1

    def _next_planned_from_sql_unlocked(
        self, flow_run_id: UUID, task_name: str
    ) -> str | None:
        cur = self._sqlite_conn.execute(
            "SELECT manifest_json FROM dag_manifests WHERE flow_run_id = ? LIMIT 1",
            (str(flow_run_id),),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        manifest = json.loads(rows[0]["manifest_json"] or "{}")
        nodes = manifest.get("nodes", [])
        cur2 = self._sqlite_conn.execute(
            "SELECT planned_node_id FROM task_runs WHERE flow_run_id = ? AND task_name = ?",
            (str(flow_run_id), task_name),
        )
        used_rows = cur2.fetchall()
        used = {row["planned_node_id"] for row in used_rows if row["planned_node_id"]}
        for node in nodes:
            if node.get("task_name") == task_name and node.get("node_id") not in used:
                return str(node.get("node_id"))
        return None

    def _rebuild_manifest_cache_from_db(self) -> None:
        with self._lock:
            self._manifest_by_task.clear()
            cur = self._sqlite_conn.execute(
                "SELECT flow_run_id, manifest_json FROM dag_manifests",
            )
            for row in cur.fetchall():
                run_id = UUID(str(row["flow_run_id"]))
                manifest = json.loads(row["manifest_json"] or "{}")
                task_to_ids: dict[str, list[str]] = {}
                for node in manifest.get("nodes", []):
                    tn = node.get("task_name")
                    nid = node.get("node_id")
                    if tn and nid is not None:
                        task_to_ids.setdefault(str(tn), []).append(str(nid))
                self._manifest_by_task[run_id] = task_to_ids

    def set_flow_state(
        self,
        run_id: UUID,
        to_state: RunState,
        transition_token: UUID,
        transition_kind: str,
        expected_version: int | None = None,
    ) -> SetStateResult:
        with self._lock:
            record = self._flows[run_id]

            if self._rust_fsm_active():
                from_state = record.state.value
                body: dict[str, Any] = {
                    "run_id": str(run_id),
                    "to_state": to_state.value,
                    "transition_token": str(transition_token),
                    "transition_kind": transition_kind,
                }
                if expected_version is not None:
                    body["expected_version"] = int(expected_version)
                use_native_persist = self._rust_native_persistence
                op = (
                    "set_flow_state_persist" if use_native_persist else "set_flow_state"
                )
                payload: dict[str, Any] = (
                    self._persist_payload(body) if use_native_persist else body
                )
                out = self._rust_fsm_call(op, payload)
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if use_native_persist and self._is_unknown_op_error(
                        err, "set_flow_state_persist"
                    ):
                        self._rust_native_persistence = False
                        out = self._rust_fsm_call("set_flow_state", body)
                    if not out.get("ok", True):
                        self._raise_from_rust_fsm_error(out.get("error", {}))
                status = str(out["status"])
                new_state = RunState(str(out["current_state"]))
                new_version = int(out["version"])
                if status == "duplicate":
                    record.state = new_state
                    record.version = new_version
                    return SetStateResult(
                        status="duplicate", state=new_state, version=new_version
                    )

                self._tokens.add(transition_token)
                record.state = new_state
                record.version = new_version
                self._events.append(
                    {
                        "event_id": str(uuid4()),
                        "run_id": str(run_id),
                        "from_state": from_state,
                        "to_state": new_state.value,
                        "kind": transition_kind,
                    }
                )
                if not self._rust_native_persistence:
                    event = self._events[-1]
                    self._insert_event_row(event)
                    self._update_flow_row(record)
                    self._insert_log_row(
                        {
                            "flow_run_id": str(run_id),
                            "task_run_id": None,
                            "level": "INFO",
                            "message": f"Flow state transition {event['from_state']} -> {event['to_state']}",
                        }
                    )
                self._persist_record(
                    {
                        "record_type": "flow_transition",
                        "run_id": str(run_id),
                        "to_state": new_state.value,
                        "kind": transition_kind,
                        "version": record.version,
                        "transition_token": str(transition_token),
                    }
                )
                return SetStateResult(
                    status="applied", state=record.state, version=record.version
                )

            if transition_token in self._tokens:
                return SetStateResult(
                    status="duplicate", state=record.state, version=record.version
                )

            if expected_version is not None and expected_version != record.version:
                raise ValueError(
                    f"version conflict expected={expected_version} actual={record.version}"
                )

            if not _legacy_is_valid_transition(record.state, to_state):
                raise ValueError(f"invalid transition {record.state} -> {to_state}")

            self._tokens.add(transition_token)
            self._events.append(
                {
                    "event_id": str(uuid4()),
                    "run_id": str(run_id),
                    "from_state": record.state.value,
                    "to_state": to_state.value,
                    "kind": transition_kind,
                }
            )
            event = self._events[-1]
            self._insert_event_row(event)

            record.state = to_state
            record.version += 1
            self._update_flow_row(record)
            self._insert_log_row(
                {
                    "flow_run_id": str(run_id),
                    "task_run_id": None,
                    "level": "INFO",
                    "message": f"Flow state transition {event['from_state']} -> {event['to_state']}",
                }
            )
            self._persist_record(
                {
                    "record_type": "flow_transition",
                    "run_id": str(run_id),
                    "to_state": to_state.value,
                    "kind": transition_kind,
                    "version": record.version,
                    "transition_token": str(transition_token),
                }
            )
            return SetStateResult(
                status="applied", state=record.state, version=record.version
            )

    def set_flow_states_batch(
        self,
        run_id: UUID,
        transitions: list[tuple[RunState, UUID, str, int | None]],
    ) -> list[SetStateResult]:
        if not transitions:
            return []
        if len(transitions) < self._FLOW_BATCH_MIN_SIZE:
            results: list[SetStateResult] = []
            for to_state, token, kind, expected_version in transitions:
                results.append(
                    self.set_flow_state(run_id, to_state, token, kind, expected_version)
                )
            return results
        with self._lock:
            if self._rust_fsm_active() and self._rust_native_persistence:
                record = self._flows[run_id]
                items: list[dict[str, Any]] = []
                for to_state, token, kind, expected_version in transitions:
                    req: dict[str, Any] = {
                        "run_id": str(run_id),
                        "to_state": to_state.value,
                        "transition_token": str(token),
                        "transition_kind": kind,
                    }
                    if expected_version is not None:
                        req["expected_version"] = int(expected_version)
                    items.append({"request": req})
                out = self._rust_fsm_call(
                    "set_flow_states_persist_batch",
                    {
                        **(
                            {}
                            if self._rust_db_bound
                            else {"db_path": str(self._sqlite_path)}
                        ),
                        "items": items,
                    },
                )
                if not out.get("ok", True):
                    err = out.get("error", {})
                    if self._is_unknown_op_error(err, "set_flow_states_persist_batch"):
                        self._rust_native_persistence = False
                    else:
                        self._raise_from_rust_fsm_error(err)
            else:
                record = self._flows[run_id]
        # Fallback and non-native paths: defer to single-op API.
        if (not self._rust_fsm_active()) or (not self._rust_native_persistence):
            results: list[SetStateResult] = []
            for to_state, token, kind, expected_version in transitions:
                results.append(
                    self.set_flow_state(run_id, to_state, token, kind, expected_version)
                )
            return results
        # Native batch succeeded: synthesize local events/history.
        out_results = out.get("results", [])
        synthesized: list[SetStateResult] = []
        prev_state = record.state
        for i, result in enumerate(out_results):
            status = str(result.get("status", ""))
            new_state = RunState(str(result.get("current_state", record.state.value)))
            new_version = int(result.get("version", record.version))
            token = transitions[i][1]
            kind = transitions[i][2]
            if status == "duplicate":
                record.state = new_state
                record.version = new_version
                synthesized.append(
                    SetStateResult(
                        status="duplicate", state=new_state, version=new_version
                    )
                )
                prev_state = record.state
                continue
            self._tokens.add(token)
            self._events.append(
                {
                    "event_id": str(uuid4()),
                    "run_id": str(run_id),
                    "from_state": prev_state.value,
                    "to_state": new_state.value,
                    "kind": kind,
                }
            )
            record.state = new_state
            record.version = new_version
            self._persist_record(
                {
                    "record_type": "flow_transition",
                    "run_id": str(run_id),
                    "to_state": new_state.value,
                    "kind": kind,
                    "version": record.version,
                    "transition_token": str(token),
                }
            )
            synthesized.append(
                SetStateResult(status="applied", state=new_state, version=new_version)
            )
            prev_state = record.state
        return synthesized

    def get_flow(self, run_id: UUID) -> FlowRunRecord:
        with self._lock:
            return self._flows[run_id]

    def get_task_run(self, task_run_id: UUID) -> TaskRunRecord:
        with self._lock:
            return self._tasks[task_run_id]

    def latest_flow(self) -> FlowRunRecord | None:
        with self._lock:
            if self._latest_flow_run_id is None:
                return None
            return self._flows[self._latest_flow_run_id]
