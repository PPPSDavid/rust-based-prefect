from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from .types import (
    RunState,
)


class GatesMixin:
    _CHILD_TERMINAL = frozenset(
        {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
    )

    def list_contributing_children(self, flow_run_id: UUID) -> list[dict[str, Any]]:
        """Return contributing child task rows (Rust when bound; else SQLite)."""
        if self._rust_fsm_active() and self._rust_db_bound:
            out = self._rust_fsm_call(
                "list_contributing_children",
                {"flow_run_id": str(flow_run_id)},
            )
            if out.get("ok", True) and "items" in out:
                return list(out.get("items") or [])
            err = out.get("error", {})
            if not self._is_unknown_op_error(err, "list_contributing_children"):
                self._raise_from_rust_fsm_error(err)
        return self._list_contributing_children_python(flow_run_id)

    def resolve_flow_terminal_state(self, flow_run_id: UUID) -> dict[str, Any]:
        """Aggregate contributing child states → flow terminal state (Rust hot path)."""
        if self._rust_fsm_active() and self._rust_db_bound:
            out = self._rust_fsm_call(
                "resolve_flow_terminal_state",
                {"flow_run_id": str(flow_run_id)},
            )
            if out.get("ok", True) and "state" in out:
                return out
            err = out.get("error", {})
            if not self._is_unknown_op_error(err, "resolve_flow_terminal_state"):
                self._raise_from_rust_fsm_error(err)
        return self._resolve_flow_terminal_state_python(flow_run_id)

    def wait_contributing_children(
        self,
        flow_run_id: UUID,
        *,
        timeout_seconds: float = 3600.0,
        poll_seconds: float = 0.05,
    ) -> None:
        """Block until all contributing children are terminal (waits deployment subflows)."""
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while time.monotonic() < deadline:
            items = self.list_contributing_children(flow_run_id)
            open_items = [
                item
                for item in items
                if str(item.get("state", ""))
                not in {s.value for s in self._CHILD_TERMINAL}
            ]
            if not open_items:
                return
            waited = False
            for item in open_items:
                dep = item.get("child_deployment_run_id")
                if dep:
                    remaining = max(0.0, deadline - time.monotonic())
                    self.wait_for_deployment_run_terminal(
                        UUID(str(dep)),
                        parent_task_run_id=UUID(str(item["id"])),
                        timeout_seconds=remaining,
                        poll_seconds=poll_seconds,
                    )
                    waited = True
            if not waited:
                # Promote due temporal gates so wait_all does not hang forever on
                # after=0 (or past-until) gates that never got GateFuture.result().
                try:
                    self.tick_gate_tasks()
                except Exception:
                    pass
                time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        raise TimeoutError(
            f"timed out waiting for contributing children of flow run {flow_run_id}"
        )

    def _list_contributing_children_python(
        self, flow_run_id: UUID
    ) -> list[dict[str, Any]]:
        rows = self._query_rows(
            """
            SELECT id, task_name, state, COALESCE(kind, 'task') AS kind,
                   child_deployment_run_id,
                   COALESCE(contribute_to_flow_state, 1) AS contribute_to_flow_state
            FROM task_runs
            WHERE flow_run_id = ?
              AND COALESCE(contribute_to_flow_state, 1) != 0
            ORDER BY seq ASC
            """,
            [str(flow_run_id)],
        )
        return [
            {
                "id": row["id"],
                "task_name": row["task_name"],
                "state": row["state"],
                "kind": row["kind"],
                "child_deployment_run_id": row["child_deployment_run_id"],
            }
            for row in rows
        ]

    def _resolve_flow_terminal_state_python(self, flow_run_id: UUID) -> dict[str, Any]:
        items = self._list_contributing_children_python(flow_run_id)
        counts = {
            "total": len(items),
            "COMPLETED": 0,
            "FAILED": 0,
            "CANCELLED": 0,
            "non_terminal": 0,
            "other": 0,
        }
        sample_failures: list[dict[str, Any]] = []
        sample_cancelled: list[dict[str, Any]] = []
        sample_incomplete: list[dict[str, Any]] = []
        for item in items:
            st = str(item.get("state", ""))
            sample = {
                "id": item.get("id"),
                "task_name": item.get("task_name"),
                "state": st,
                "kind": item.get("kind"),
                "child_deployment_run_id": item.get("child_deployment_run_id"),
            }
            if st == "COMPLETED":
                counts["COMPLETED"] += 1
            elif st == "FAILED":
                counts["FAILED"] += 1
                if len(sample_failures) < 8:
                    sample_failures.append(sample)
            elif st == "CANCELLED":
                counts["CANCELLED"] += 1
                if len(sample_cancelled) < 8:
                    sample_cancelled.append(sample)
            elif st in {"SCHEDULED", "PENDING", "RUNNING", "PAUSED", "CANCELLING"}:
                counts["non_terminal"] += 1
                if len(sample_incomplete) < 8:
                    sample_incomplete.append(sample)
            else:
                counts["other"] += 1
                counts["FAILED"] += 1
                if len(sample_failures) < 8:
                    sample_failures.append(sample)
        if not items:
            kind = "empty"
            state = "COMPLETED"
        elif counts["CANCELLED"] > 0:
            kind = "child_cancelled"
            state = "CANCELLED"
        elif counts["FAILED"] > 0:
            kind = "child_failed"
            state = "FAILED"
        elif counts["non_terminal"] > 0:
            kind = "incomplete_children"
            state = "FAILED"
        else:
            kind = "all_completed"
            state = "COMPLETED"
        return {
            "ok": True,
            "state": state,
            "kind": kind,
            "counts": counts,
            "sample_failures": sample_failures,
            "sample_cancelled": sample_cancelled,
            "sample_incomplete": sample_incomplete,
            "_via": "python",
        }

    def pause_flow_for_gate(self, flow_run_id: UUID, gate_task_run_id: UUID) -> None:
        flow = self.get_flow(flow_run_id)
        if flow.state == RunState.RUNNING:
            try:
                self.set_flow_state(flow_run_id, RunState.PAUSED, uuid4(), "gate_wait")
            except ValueError:
                pass
        self._persist_record(
            {
                "record_type": "gate_wait",
                "flow_run_id": str(flow_run_id),
                "gate_task_run_id": str(gate_task_run_id),
            }
        )

    def resume_flow_from_gate(self, flow_run_id: UUID) -> None:
        flow = self.get_flow(flow_run_id)
        if flow.state == RunState.PAUSED:
            try:
                self.set_flow_state(flow_run_id, RunState.RUNNING, uuid4(), "gate_open")
            except ValueError:
                pass

    def complete_gate_task(self, task_run_id: UUID) -> None:
        task = self.get_task_run(task_run_id)
        if task.state == RunState.COMPLETED:
            return
        if task.state == RunState.CANCELLED:
            return
        if task.state == RunState.SCHEDULED:
            self.record_task_event(task_run_id, "task_pending", {"gate": True})
        if self.get_task_run(task_run_id).state in {
            RunState.SCHEDULED,
            RunState.PENDING,
        }:
            self.record_task_event(
                task_run_id, "task_running", {"gate": True, "opened_at": self._now()}
            )
            self.record_task_event(
                task_run_id,
                "task_completed",
                {"gate": True, "opened_at": self._now()},
            )

    def cancel_gate_task(self, task_run_id: UUID) -> None:
        task = self.get_task_run(task_run_id)
        if task.state in {RunState.COMPLETED, RunState.CANCELLED, RunState.FAILED}:
            return
        if task.state == RunState.SCHEDULED:
            self.record_task_event(task_run_id, "task_pending", {"gate": True})
        if self.get_task_run(task_run_id).state in {
            RunState.SCHEDULED,
            RunState.PENDING,
        }:
            self.record_task_event(
                task_run_id,
                "task_running",
                {"gate": True},
            )
            self.record_task_event(
                task_run_id,
                "task_cancelled",
                {"gate": True, "error": "parent flow cancelled"},
            )

    def fail_gate_task(self, task_run_id: UUID, error: str) -> None:
        task = self.get_task_run(task_run_id)
        if task.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            return
        if task.state == RunState.SCHEDULED:
            self.record_task_event(task_run_id, "task_pending", {"gate": True})
        if self.get_task_run(task_run_id).state in {
            RunState.SCHEDULED,
            RunState.PENDING,
            RunState.RUNNING,
        }:
            self.record_task_event(
                task_run_id,
                "task_failed",
                {"gate": True, "error": error},
            )

    def tick_gate_tasks(self) -> int:
        """Promote due gate tasks (PENDING → COMPLETED). Prefers Rust hot path when bound."""
        rust = self._rust_deployment_dispatch("task_tick_gate_tasks", {})
        if rust is not None and rust.get("ok"):
            promoted = int(rust.get("promoted", 0))
            if promoted:
                self._sync_gate_tasks_from_sqlite()
                return promoted
        return self._tick_gate_tasks_python()

    def _tick_gate_tasks_python(self) -> int:
        now = self._now()
        now_dt = datetime.now(UTC)
        due_ids: list[UUID] = []
        rows = self._query_rows(
            """
            SELECT id FROM task_runs
            WHERE kind = 'gate' AND state = 'PENDING'
              AND gate_open_at IS NOT NULL AND gate_open_at <= ?
            """,
            [now],
        )
        due_ids.extend(UUID(str(row["id"])) for row in rows)
        with self._lock:
            for task in self._tasks.values():
                if task.kind != "gate" or task.state != RunState.PENDING:
                    continue
                if not task.gate_open_at:
                    continue
                open_raw = task.gate_open_at.replace("Z", "+00:00")
                open_at = datetime.fromisoformat(open_raw)
                if open_at.tzinfo is None:
                    open_at = open_at.replace(tzinfo=UTC)
                if open_at <= now_dt and task.task_run_id not in due_ids:
                    due_ids.append(task.task_run_id)
        for task_id in due_ids:
            self.complete_gate_task(task_id)
        return len(due_ids)

    def _sync_gate_tasks_from_sqlite(self) -> None:
        """Refresh in-memory gate task states after Rust promotion tick."""
        rows = self._query_rows(
            "SELECT id, state, version FROM task_runs WHERE kind = 'gate'",
            [],
        )
        for row in rows:
            tid = UUID(str(row["id"]))
            task = self._tasks.get(tid)
            if task is None:
                continue
            try:
                task.state = RunState(str(row["state"]))
                task.version = int(row["version"])
            except ValueError:
                continue
