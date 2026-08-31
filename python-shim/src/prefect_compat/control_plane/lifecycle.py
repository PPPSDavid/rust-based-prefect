from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from .types import (
    LIFECYCLE_LOG as _LIFECYCLE_LOG,
)
from .types import (
    SUBFLOW_MAX_DEPTH,
    RunState,
)


class LifecycleMixin:
    def _set_flow_cancelled_internal(self, flow_run_id: UUID) -> None:
        flow = self._flows.get(flow_run_id)
        if flow is None:
            return
        if flow.state not in {
            RunState.SCHEDULED,
            RunState.PENDING,
            RunState.RUNNING,
            RunState.PAUSED,
        }:
            return
        try:
            self.set_flow_state(
                flow_run_id, RunState.CANCELLED, uuid4(), "parent_cancel"
            )
        except ValueError:
            return
        now = self._now()
        self._sqlite_conn.execute(
            """
            UPDATE task_runs
            SET state = 'CANCELLED', updated_at = ?
            WHERE flow_run_id = ? AND state IN ('SCHEDULED','PENDING','RUNNING')
            """,
            [now, str(flow_run_id)],
        )
        for task in self._tasks.values():
            if task.flow_run_id == flow_run_id and task.state in {
                RunState.SCHEDULED,
                RunState.PENDING,
                RunState.RUNNING,
            }:
                task.state = RunState.CANCELLED

    def _propagate_cancel_to_subflows(self, root_flow_run_id: UUID) -> None:
        """BFS cancel of inline/deployment child flow runs and linked deployment runs."""
        frontier: list[UUID] = [root_flow_run_id]
        visited: set[UUID] = {root_flow_run_id}
        for _ in range(SUBFLOW_MAX_DEPTH):
            if not frontier:
                break
            next_frontier: list[UUID] = []
            for parent_id in frontier:
                for row in self._cancel_deployment_runs_for_parent_flow(parent_id):
                    fid = row.get("flow_run_id")
                    if fid:
                        child_flow_id = UUID(str(fid))
                        if child_flow_id not in visited:
                            self._set_flow_cancelled_internal(child_flow_id)
                            visited.add(child_flow_id)
                            next_frontier.append(child_flow_id)
                for flow in list(self._flows.values()):
                    if flow.parent_flow_run_id != parent_id:
                        continue
                    if flow.run_id in visited:
                        continue
                    if flow.state not in {
                        RunState.SCHEDULED,
                        RunState.PENDING,
                        RunState.RUNNING,
                    }:
                        continue
                    self._set_flow_cancelled_internal(flow.run_id)
                    visited.add(flow.run_id)
                    next_frontier.append(flow.run_id)
            frontier = next_frontier

    def _cancel_inline_child_flow_runs(self, parent_flow_run_id: UUID) -> None:
        """Deprecated: use _propagate_cancel_to_subflows from cancel_flow_run."""
        self._propagate_cancel_to_subflows(parent_flow_run_id)

    def is_scheduling_held(self, flow_run_id: UUID) -> bool:
        """True when operator pause blocks starting new task runs."""
        with self._lock:
            return self._is_scheduling_held_unlocked(flow_run_id)

    def _is_scheduling_held_unlocked(self, flow_run_id: UUID) -> bool:
        life = self._lifecycle_by_flow.get(str(flow_run_id))
        if not life:
            return False
        # Cancel holds immediately while terminate settles (same race as pause).
        if life.get("lifecycle_action") == "cancel":
            return True
        if life.get("lifecycle_action") != "pause":
            return False
        if life.get("pause_drain_pending"):
            return True
        # Terminate holds immediately — do not allow new submits between
        # lifecycle write and PAUSED settle (race window for concurrent runners).
        if life.get("interrupt_mode") == "terminate":
            return True
        flow = self._flows.get(flow_run_id)
        return bool(flow and flow.state == RunState.PAUSED)

    def has_operator_pause(self, flow_run_id: UUID) -> bool:
        life = self._lifecycle_by_flow.get(str(flow_run_id))
        return bool(life and life.get("lifecycle_action") == "pause")

    def has_operator_interrupt(self, flow_run_id: UUID) -> bool:
        """True while cancel or pause lifecycle is in progress (any flow state)."""
        life = self._lifecycle_by_flow.get(str(flow_run_id))
        return bool(life and life.get("lifecycle_action") in {"pause", "cancel"})

    def pause_flow_run(self, flow_run_id: UUID, mode: str | Any) -> dict[str, Any]:
        """Operator pause. ``mode`` must be ``drain`` or ``terminate`` (required)."""
        from ..lifecycle import InterruptMode, parse_interrupt_mode

        interrupt = parse_interrupt_mode(mode)
        detail = self.get_flow_run_detail(flow_run_id)
        if detail is None:
            raise ValueError("flow run not found")
        state = str(detail["state"])
        if state in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise ValueError(f"cannot pause from state {state}")
        # Operator pause only from active scheduling states — not gate-only PAUSED.
        if state not in {"SCHEDULED", "PENDING", "RUNNING"}:
            raise ValueError(
                f"cannot pause from state {state} "
                "(operator pause requires SCHEDULED/PENDING/RUNNING; "
                "gate waits are not operator pauses)"
            )

        if interrupt is InterruptMode.DRAIN:
            running = self._count_running_tasks(flow_run_id)
            pending_drain = running > 0 and state == "RUNNING"
            self._set_lifecycle(
                flow_run_id,
                lifecycle_action="pause",
                interrupt_mode=interrupt.value,
                pause_drain_pending=pending_drain,
                lifecycle_summary=(
                    f"Paused (drain) — waiting for {running} task(s)"
                    if pending_drain
                    else "Paused (drain)"
                ),
            )
            if not pending_drain:
                if state != "PAUSED":
                    try:
                        self.set_flow_state(
                            flow_run_id,
                            RunState.PAUSED,
                            uuid4(),
                            "operator_pause_drain",
                        )
                    except ValueError:
                        pass
            return self.get_flow_run_detail(flow_run_id) or detail

        # terminate: cancel RUNNING task rows first (fence late COMPLETED), then
        # kill registered process workers. Thread-pool bodies may continue until
        # exit; late COMPLETED is still fenced by CANCELLED state.
        from ..process_workers import task_process_registry

        self._set_lifecycle(
            flow_run_id,
            lifecycle_action="pause",
            interrupt_mode=interrupt.value,
            pause_drain_pending=False,
            lifecycle_summary="Paused (terminate) — in-flight tasks interrupted",
        )
        running_ids = [
            tid
            for tid, task in list(self._tasks.items())
            if str(task.flow_run_id) == str(flow_run_id)
            and task.state == RunState.RUNNING
        ]
        for tid in running_ids:
            try:
                self.record_task_event(
                    tid,
                    "task_cancelled",
                    {"interrupt_reason": "terminated_by_pause"},
                )
            except Exception:
                with self._lock:
                    task = self._tasks.get(tid)
                    if task and task.state == RunState.RUNNING:
                        task.state = RunState.CANCELLED
                        self._update_task_row(task)
        killed = task_process_registry().terminate_flow_workers(flow_run_id)
        if not killed and running_ids:
            _LIFECYCLE_LOG.warning(
                "terminate pause flow %s: no registered process workers; "
                "in-flight thread bodies are cooperative-only",
                flow_run_id,
            )
        try:
            self.set_flow_state(
                flow_run_id, RunState.PAUSED, uuid4(), "operator_pause_terminate"
            )
        except ValueError:
            pass
        refreshed = self.get_flow_run_detail(flow_run_id)
        if refreshed is None:
            raise ValueError("flow run not found")
        if killed:
            refreshed = dict(refreshed)
            refreshed["terminated_task_run_ids"] = killed
        self._release_gcl_holders_for_flow(flow_run_id)
        return refreshed

    def resume_flow_run(self, flow_run_id: UUID) -> dict[str, Any]:
        """Resume an operator-paused flow run (not gate-only PAUSED).

        After **terminate** pause:
        - deployment-backed runs → ``retry_flow_run`` (new attempt with P1
          ``resume_from`` so COMPLETED skips and interrupted tasks re-run)
        - in-process → ``prepare_resume`` so the next ``@flow()`` invoke skips
          completed nodes

        After **drain** pause: flip back to RUNNING (or COMPLETED if the body
        already stored a result and nothing remains).
        """
        detail = self.get_flow_run_detail(flow_run_id)
        if detail is None:
            raise ValueError("flow run not found")
        if not self.has_operator_pause(flow_run_id):
            raise ValueError(
                "resume requires an operator pause (lifecycle_action=pause); "
                "gate waits use gate open, not resume"
            )
        state = str(detail["state"])
        if state not in {"PAUSED", "RUNNING"}:
            raise ValueError(f"cannot resume from state {state}")
        life = dict(self._lifecycle_by_flow.get(str(flow_run_id), {}))
        if life.get("pause_drain_pending"):
            raise ValueError("cannot resume while drain pause is still pending")

        prior_mode = life.get("interrupt_mode")
        if prior_mode == "terminate":
            # P3.2d: re-drive interrupted work via P1 resume lineage.
            # In-process: queue prepare_resume for the *next* @flow() invoke
            # (new run). Keep this run PAUSED — flipping to RUNNING would leave
            # a zombie with no attached Python body.
            self._set_lifecycle(
                flow_run_id,
                lifecycle_action="resume",
                interrupt_mode=None,
                pause_drain_pending=False,
                lifecycle_summary="Resume after terminate — re-execute interrupted",
            )
            if detail.get("deployment_id"):
                retried = self.retry_flow_run(flow_run_id)
                retried = dict(retried)
                retried["resumed_via"] = "retry_after_terminate"
                retried["resume_from_flow_run_id"] = str(flow_run_id)
                return retried
            self.prepare_resume(flow_run_id)
            # Terminalize this attempt — successor is a new flow run via
            # prepare_resume. Leaving PAUSED/RUNNING would strand a zombie.
            if state == "PAUSED":
                try:
                    self.set_flow_state(
                        flow_run_id,
                        RunState.CANCELLED,
                        uuid4(),
                        "superseded_by_terminate_resume",
                    )
                except ValueError:
                    pass
            refreshed = self.get_flow_run_detail(flow_run_id)
            if refreshed is None:
                raise ValueError("flow run not found")
            refreshed = dict(refreshed)
            refreshed["resumed_via"] = "prepare_resume"
            refreshed["resume_from_flow_run_id"] = str(flow_run_id)
            return refreshed

        has_result = flow_run_id in self._flow_results
        pending_left = self._count_nonterminal_tasks(flow_run_id) > 0

        if state == "PAUSED":
            try:
                self.set_flow_state(
                    flow_run_id, RunState.RUNNING, uuid4(), "operator_resume"
                )
            except ValueError as exc:
                refreshed = self.get_flow_run_detail(flow_run_id)
                if not (refreshed and refreshed["state"] == "RUNNING"):
                    raise ValueError(str(exc)) from exc

        if has_result and not pending_left:
            current = self.get_flow(flow_run_id)
            if current.state == RunState.RUNNING:
                try:
                    self.set_flow_state(
                        flow_run_id,
                        RunState.COMPLETED,
                        uuid4(),
                        "complete_after_pause",
                        expected_version=current.version,
                    )
                except ValueError:
                    pass

        self._set_lifecycle(
            flow_run_id,
            lifecycle_action="resume",
            interrupt_mode=None,
            pause_drain_pending=False,
            lifecycle_summary=None,
        )
        refreshed = self.get_flow_run_detail(flow_run_id)
        if refreshed is None:
            raise ValueError("flow run not found")
        return refreshed

    def _count_running_tasks(self, flow_run_id: UUID) -> int:
        with self._lock:
            n = 0
            for task in self._tasks.values():
                if (
                    str(task.flow_run_id) == str(flow_run_id)
                    and task.state == RunState.RUNNING
                ):
                    n += 1
            return n

    def _count_nonterminal_tasks(self, flow_run_id: UUID) -> int:
        terminal = {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
        with self._lock:
            n = 0
            for task in self._tasks.values():
                if (
                    str(task.flow_run_id) == str(flow_run_id)
                    and task.state not in terminal
                ):
                    n += 1
            return n

    def _set_lifecycle(
        self,
        flow_run_id: UUID,
        *,
        lifecycle_action: str | None,
        interrupt_mode: str | None,
        pause_drain_pending: bool = False,
        lifecycle_summary: str | None = None,
    ) -> None:
        key = str(flow_run_id)
        if lifecycle_action is None and interrupt_mode is None:
            self._lifecycle_by_flow.pop(key, None)
            return
        entry = {
            "lifecycle_action": lifecycle_action,
            "interrupt_mode": interrupt_mode,
            "pause_drain_pending": bool(pause_drain_pending),
            "lifecycle_summary": lifecycle_summary,
        }
        self._lifecycle_by_flow[key] = entry
        self._persist_record(
            {
                "record_type": "flow_lifecycle",
                "flow_run_id": key,
                **entry,
            }
        )

    def _maybe_settle_drain_pause(self, flow_run_id: UUID) -> None:
        life = self._lifecycle_by_flow.get(str(flow_run_id))
        if not life or not life.get("pause_drain_pending"):
            return
        if life.get("interrupt_mode") != "drain":
            return
        if self._count_running_tasks(flow_run_id) > 0:
            return
        self._set_lifecycle(
            flow_run_id,
            lifecycle_action="pause",
            interrupt_mode="drain",
            pause_drain_pending=False,
            lifecycle_summary="Paused (drain)",
        )
        try:
            flow = self.get_flow(flow_run_id)
        except Exception:
            return
        if flow.state == RunState.RUNNING:
            try:
                self.set_flow_state(
                    flow_run_id,
                    RunState.PAUSED,
                    uuid4(),
                    "operator_pause_drain_settled",
                )
            except ValueError:
                pass

    def cancel_flow_run(self, flow_run_id: UUID) -> dict[str, Any]:
        detail = self.get_flow_run_detail(flow_run_id)
        if detail is None:
            raise ValueError("flow run not found")
        state = str(detail["state"])
        if state == "CANCELLED":
            return detail
        if state in {"COMPLETED", "FAILED"}:
            with self._lock:
                self._propagate_cancel_to_subflows(flow_run_id)
            return detail
        if state not in {"SCHEDULED", "PENDING", "RUNNING", "PAUSED"}:
            raise ValueError(f"cannot cancel from state {state}")

        self._set_lifecycle(
            flow_run_id,
            lifecycle_action="cancel",
            interrupt_mode="terminate",
            pause_drain_pending=False,
            lifecycle_summary="Cancelled (terminate)",
        )

        from ..process_workers import task_process_registry

        # Fence late COMPLETED via task FSM events before SIGTERM/SIGKILL.
        cancellable = {
            RunState.SCHEDULED,
            RunState.PENDING,
            RunState.RUNNING,
        }
        with self._lock:
            task_ids = [
                tid
                for tid, task in self._tasks.items()
                if str(task.flow_run_id) == str(flow_run_id)
                and task.state in cancellable
            ]
        for tid in task_ids:
            try:
                self.record_task_event(
                    tid,
                    "task_cancelled",
                    {"interrupt_reason": "terminated_by_cancel"},
                )
            except Exception:
                with self._lock:
                    task = self._tasks.get(tid)
                    if task and task.state in cancellable:
                        task.state = RunState.CANCELLED
                        self._update_task_row(task)

        killed = task_process_registry().terminate_flow_workers(flow_run_id)
        if not killed and task_ids:
            _LIFECYCLE_LOG.warning(
                "cancel flow %s: no registered process workers; "
                "in-flight thread bodies are cooperative-only",
                flow_run_id,
            )

        token = uuid4()
        try:
            self.set_flow_state(flow_run_id, RunState.CANCELLED, token, "user_cancel")
        except ValueError:
            refreshed = self.get_flow_run_detail(flow_run_id)
            if refreshed and refreshed["state"] == "CANCELLED":
                if killed:
                    refreshed = dict(refreshed)
                    refreshed["terminated_task_run_ids"] = killed
                self._release_gcl_holders_for_flow(flow_run_id)
                return refreshed
            raise

        with self._lock:
            self._propagate_cancel_to_subflows(flow_run_id)
        self._release_gcl_holders_for_flow(flow_run_id)
        refreshed = self.get_flow_run_detail(flow_run_id)
        if refreshed is None:
            raise ValueError("flow run not found")
        if killed:
            refreshed = dict(refreshed)
            refreshed["terminated_task_run_ids"] = killed
        return refreshed

    def retry_flow_run(self, flow_run_id: UUID) -> dict[str, Any]:
        rows = self._query_rows(
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
        deployment_id = UUID(str(rows[0]["deployment_id"]))
        requested = json.loads(rows[0]["requested_parameters"] or "{}")
        return self.trigger_deployment_run(
            deployment_id,
            parameters=requested,
            resume_from_flow_run_id=flow_run_id,
        )
