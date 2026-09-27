"""Deployment retry that re-attempts the same flow run id.

Completed task runs stay completed and are not recomputed when the static
resume contract can restore their results. Quick Run does not use this module.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from .cancellation import FlowRunCancelled
from .result_codec import ResultEncodeError, decode_task_result, encode_task_result
from .runtime import RunState

_RETRYABLE = {
    RunState.FAILED,
    RunState.CANCELLED,
    RunState.COMPLETED,
    RunState.PAUSED,
}
_TERMINAL = {RunState.FAILED, RunState.CANCELLED, RunState.COMPLETED}

# (flow_run_id, planned_node_id, map_index) -> (task_run_id, value, fingerprint)
_REMEMBERED: dict[tuple[str, str, int], tuple[str, Any, str]] = {}


def same_run_reenter_id(dep_run: dict[str, Any] | None) -> UUID | None:
    """Flow run to re-enter when this deployment run is bound to it."""
    if not dep_run:
        return None
    resume_from = dep_run.get("resume_from_flow_run_id")
    bound = dep_run.get("flow_run_id")
    if not resume_from or not bound or str(resume_from) != str(bound):
        return None
    return UUID(str(bound))


def schedule_same_run_retry(plane: Any, flow_run_id: UUID) -> dict[str, Any]:
    """Queue another attempt of ``flow_run_id`` and reopen that run."""
    detail = plane.get_flow_run_detail(flow_run_id)
    if detail is None:
        raise ValueError("flow run not found")
    # Deployment backing is checked first so non-deployment clients always get 409,
    # including when the run is not in a retryable state.
    deployment_id, requested = _deployment_for_flow_run(plane, flow_run_id)
    _require_runnable_deployment(plane, deployment_id)
    state = RunState(str(detail["state"]))
    if state not in _RETRYABLE:
        raise ValueError(f"cannot retry from state {state.value}")
    _reopen_flow_run(plane, flow_run_id, state)
    _enqueue_bound_attempt(plane, deployment_id, flow_run_id, requested)
    refreshed = plane.get_flow_run_detail(flow_run_id)
    if refreshed is None:
        raise ValueError("flow run not found")
    return refreshed


def begin_same_run_reentry(plane: Any, flow_run_id: UUID) -> Any:
    """Mark ``flow_run_id`` running and ready to skip completed tasks."""
    record = plane.get_flow(flow_run_id)
    if record.state == RunState.CANCELLED:
        raise FlowRunCancelled(f"flow run {flow_run_id} was cancelled")
    _arm_same_run_skips(plane, record)
    if record.state == RunState.PENDING:
        plane.set_flow_state(
            flow_run_id,
            RunState.RUNNING,
            uuid4(),
            "start",
            expected_version=record.version,
        )
    elif record.state == RunState.PAUSED:
        plane.set_flow_state(
            flow_run_id,
            RunState.RUNNING,
            uuid4(),
            "retry_reopen",
            expected_version=record.version,
        )
    elif record.state != RunState.RUNNING:
        raise ValueError(f"cannot reenter flow run from state {record.state.value}")
    armed = plane.get_flow(flow_run_id)
    _arm_same_run_skips(plane, armed)
    return armed


def remember_task_value_for_retry(
    plane: Any,
    flow_run_id: UUID,
    task_run_id: UUID,
    task_name: str,
    planned_node_id: str | None,
    value: Any,
    *,
    persist_result: bool,
    map_index: int | None,
    input_fingerprint: str | None,
) -> None:
    """Remember a completed value so a later same-run retry can skip it."""
    if not planned_node_id or not input_fingerprint:
        return
    map_key = -1 if map_index is None else int(map_index)
    _REMEMBERED[(str(flow_run_id), planned_node_id, map_key)] = (
        str(task_run_id),
        value,
        input_fingerprint,
    )
    if value is None or persist_result:
        return
    _store_unpersisted_payload(
        plane,
        flow_run_id,
        task_run_id,
        task_name,
        planned_node_id,
        value,
        map_key,
        input_fingerprint,
    )


def reused_same_run_value(
    plane: Any,
    flow_run_id: UUID,
    planned_node_id: str | None,
    input_fingerprint: str | None,
    map_index: int | None = None,
) -> tuple[str, str | None, Any] | None:
    """Return ``(task_run_id, planned_node_id, value)`` when a completed task is reused."""
    record = plane._flows.get(flow_run_id)
    if (
        record is None
        or not record.same_run_retry
        or not record.resume_skips_enabled
        or record.effective_graph_mode != "static"
        or not planned_node_id
        or not input_fingerprint
    ):
        return None
    map_key = -1 if map_index is None else int(map_index)
    remembered = _REMEMBERED.get((str(flow_run_id), planned_node_id, map_key))
    if remembered is not None and remembered[2] == input_fingerprint:
        reused = _completed_task(plane, flow_run_id, remembered[0])
        if reused is not None:
            return (str(reused.task_run_id), reused.planned_node_id, remembered[1])
    return _reused_from_sql(
        plane, record, flow_run_id, planned_node_id, input_fingerprint, map_key
    )


def _deployment_for_flow_run(plane: Any, flow_run_id: UUID) -> tuple[UUID, dict[str, Any]]:
    rows = plane._query_rows(
        """
        SELECT deployment_id, requested_parameters
        FROM deployment_runs
        WHERE flow_run_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [str(flow_run_id)],
    )
    if not rows:
        raise ValueError("flow run is not deployment-backed")
    requested = json.loads(rows[0]["requested_parameters"] or "{}")
    if not isinstance(requested, dict):
        requested = {}
    return UUID(str(rows[0]["deployment_id"])), requested


def _require_runnable_deployment(plane: Any, deployment_id: UUID) -> dict[str, Any]:
    deployment = plane.get_deployment(deployment_id)
    if deployment is None:
        raise ValueError("deployment not found")
    if deployment["paused"]:
        raise ValueError("deployment is paused")
    strategy = str(deployment.get("collision_strategy") or "ENQUEUE").upper()
    limit = deployment.get("concurrency_limit")
    if (
        limit is not None
        and strategy == "CANCEL_NEW"
        and plane._count_exec_runs(str(deployment_id)) >= int(limit)
    ):
        raise ValueError("concurrency limit reached")
    return deployment


def _enqueue_bound_attempt(
    plane: Any,
    deployment_id: UUID,
    flow_run_id: UUID,
    requested: dict[str, Any],
) -> None:
    deployment = plane.get_deployment(deployment_id)
    if deployment is None:
        raise ValueError("deployment not found")
    resolved = dict(deployment.get("default_parameters") or {})
    resolved.update(requested)
    with plane._lock:
        plane._ensure_resume_schema()
        now = plane._now()
        plane._sqlite_conn.execute(
            """
            INSERT INTO deployment_runs
            (id,deployment_id,status,requested_parameters,resolved_parameters,idempotency_key,
             worker_name,lease_until,flow_run_id,error,parent_flow_run_id,parent_task_run_id,
             parent_deployment_run_id,resume_from_flow_run_id,
             created_at,updated_at,started_at,finished_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                str(uuid4()),
                str(deployment_id),
                "SCHEDULED",
                json.dumps(requested),
                json.dumps(resolved),
                None,
                None,
                None,
                str(flow_run_id),
                None,
                None,
                None,
                None,
                str(flow_run_id),
                now,
                now,
                None,
                None,
            ],
        )


def _reopen_flow_run(plane: Any, flow_run_id: UUID, state: RunState) -> None:
    record = plane.get_flow(flow_run_id)
    record.flow_attempt_number = int(record.flow_attempt_number or 1) + 1
    with plane._lock:
        plane._ensure_resume_schema()
        plane._sqlite_conn.execute(
            "UPDATE flow_runs SET flow_attempt_number = ? WHERE id = ?",
            [record.flow_attempt_number, str(flow_run_id)],
        )
    if state in _TERMINAL:
        plane.set_flow_state(
            flow_run_id,
            RunState.PENDING,
            uuid4(),
            "retry_reopen",
            expected_version=record.version,
        )
    elif state == RunState.PAUSED:
        plane.set_flow_state(
            flow_run_id,
            RunState.RUNNING,
            uuid4(),
            "retry_reopen",
            expected_version=record.version,
        )
    _arm_same_run_skips(plane, plane.get_flow(flow_run_id))


def _arm_same_run_skips(plane: Any, record: Any) -> None:
    record.same_run_retry = True
    if record.effective_graph_mode == "static" and not record.contract_mismatch:
        record.resume_skips_enabled = True
    plane._resume_lookups_enabled = True
    plane._reserved_planned_ids.pop(record.run_id, None)


def _completed_task(plane: Any, flow_run_id: UUID, task_run_id: str) -> Any | None:
    task = plane._tasks.get(UUID(task_run_id))
    if task is None:
        return None
    if task.state != RunState.COMPLETED or str(task.flow_run_id) != str(flow_run_id):
        return None
    return task


def _reused_from_sql(
    plane: Any,
    record: Any,
    flow_run_id: UUID,
    planned_node_id: str,
    input_fingerprint: str,
    map_key: int,
) -> tuple[str, str | None, Any] | None:
    lineage_id = record.resume_lineage_id or record.run_id
    rows = plane._query_rows(
        """
        SELECT is_none_result, has_payload, payload_json, input_fingerprint, source_task_run_id
        FROM task_result_cache
        WHERE lineage_id = ? AND planned_node_id = ? AND map_index = ?
        LIMIT 1
        """,
        [str(lineage_id), planned_node_id, map_key],
    )
    if not rows:
        return None
    row = rows[0]
    if str(row["input_fingerprint"] or "") != input_fingerprint:
        return None
    task = _completed_task(plane, flow_run_id, str(row["source_task_run_id"]))
    if task is None:
        return None
    if int(row["is_none_result"] or 0) == 1:
        return (str(task.task_run_id), task.planned_node_id, None)
    if int(row["has_payload"] or 0) != 1 or row["payload_json"] is None:
        return None
    try:
        value = decode_task_result(str(row["payload_json"]))
    except Exception:
        return None
    return (str(task.task_run_id), task.planned_node_id, value)


def _store_unpersisted_payload(
    plane: Any,
    flow_run_id: UUID,
    task_run_id: UUID,
    task_name: str,
    planned_node_id: str,
    value: Any,
    map_key: int,
    input_fingerprint: str,
) -> None:
    try:
        payload_json = encode_task_result(value)
    except ResultEncodeError:
        return
    record = plane._flows.get(flow_run_id)
    lineage_id = flow_run_id
    if record is not None and record.resume_lineage_id is not None:
        lineage_id = record.resume_lineage_id
    now = plane._now()
    with plane._lock:
        plane._ensure_resume_schema()
        plane._sqlite_conn.execute(
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
                0,
                1,
                payload_json,
                input_fingerprint,
                str(flow_run_id),
                str(task_run_id),
                now,
            ],
        )
