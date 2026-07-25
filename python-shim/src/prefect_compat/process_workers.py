"""Killable process workers for cancel / terminate-pause (P3.2c).

CPython threads cannot be forcibly stopped. Task bodies that must honor
``InterruptMode.TERMINATE`` run in child processes registered here so the
parent can SIGTERM → grace → SIGKILL and fence late completions.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import signal
import time
from dataclasses import dataclass, field
from queue import Empty
from threading import RLock
from typing import Any, Callable
from uuid import UUID

_LOG = logging.getLogger("ironflow.process_workers")


class ProcessWorkerTerminated(RuntimeError):
    """Child process exited (or was killed) before returning a result."""


def terminate_grace_seconds() -> float:
    raw = os.getenv("IRONFLOW_TASK_TERMINATE_GRACE_SECONDS", "2").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 2.0


@dataclass
class _WorkerEntry:
    flow_run_id: UUID
    task_run_id: UUID
    process: mp.Process
    generation: int


@dataclass
class TaskProcessRegistry:
    """Parent-side ``task_run_id → process`` registry for terminate/cancel."""

    _lock: RLock = field(default_factory=RLock)
    _by_task: dict[str, _WorkerEntry] = field(default_factory=dict)
    _generation: int = 0

    def register(
        self, flow_run_id: UUID, task_run_id: UUID, process: mp.Process
    ) -> int:
        with self._lock:
            self._generation += 1
            gen = self._generation
            self._by_task[str(task_run_id)] = _WorkerEntry(
                flow_run_id=flow_run_id,
                task_run_id=task_run_id,
                process=process,
                generation=gen,
            )
            return gen

    def unregister(self, task_run_id: UUID, generation: int | None = None) -> None:
        with self._lock:
            key = str(task_run_id)
            entry = self._by_task.get(key)
            if entry is None:
                return
            if generation is not None and entry.generation != generation:
                return
            self._by_task.pop(key, None)

    def snapshot_for_flow(self, flow_run_id: UUID) -> list[_WorkerEntry]:
        with self._lock:
            return [
                e
                for e in self._by_task.values()
                if str(e.flow_run_id) == str(flow_run_id)
            ]

    def terminate_flow_workers(
        self, flow_run_id: UUID, *, grace_seconds: float | None = None
    ) -> list[str]:
        """SIGTERM then SIGKILL in-flight workers for a flow run.

        Returns task_run_id strings that were targeted.
        """
        grace = terminate_grace_seconds() if grace_seconds is None else grace_seconds
        entries = self.snapshot_for_flow(flow_run_id)
        killed: list[str] = []
        for entry in entries:
            proc = entry.process
            if not proc.is_alive():
                self.unregister(entry.task_run_id, entry.generation)
                continue
            tid = str(entry.task_run_id)
            try:
                proc.terminate()  # SIGTERM on POSIX
            except Exception as exc:
                _LOG.warning("terminate(%s) failed: %s", tid, exc)
            deadline = time.monotonic() + grace
            while proc.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            if proc.is_alive():
                try:
                    proc.kill()  # SIGKILL
                except Exception as exc:
                    _LOG.warning("kill(%s) failed: %s", tid, exc)
                proc.join(timeout=1.0)
            self.unregister(entry.task_run_id, entry.generation)
            killed.append(tid)
        return killed


_REGISTRY = TaskProcessRegistry()


def task_process_registry() -> TaskProcessRegistry:
    return _REGISTRY


def _child_main(
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    result_queue: mp.Queue[Any],
) -> None:
    try:
        result_queue.put(("ok", fn(*args, **kwargs)))
    except BaseException as exc:  # noqa: BLE001 — marshal to parent
        result_queue.put(("err", repr(exc)))


def run_in_registered_process(
    *,
    flow_run_id: UUID,
    task_run_id: UUID,
    fn: Callable[..., Any],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    registry: TaskProcessRegistry | None = None,
) -> Any:
    """Run ``fn`` in a child process registered for terminate/cancel.

    Registration happens before ``Process.start`` so an operator cancel/terminate
    from another thread can find the worker while this call waits for a result.
    If the child is SIGTERM/SIGKILL'd, this raises ``ProcessWorkerTerminated``
    instead of hanging on the result queue forever.
    """
    reg = registry or _REGISTRY
    ctx = mp.get_context("spawn")
    result_queue: mp.Queue[Any] = ctx.Queue()
    proc = ctx.Process(
        target=_child_main,
        args=(fn, args, kwargs or {}, result_queue),
        name=f"ironflow-task-{task_run_id}",
        daemon=True,
    )
    gen = reg.register(flow_run_id, task_run_id, proc)
    proc.start()
    try:
        while True:
            try:
                status, payload = result_queue.get(timeout=0.1)
                break
            except Empty:
                if not proc.is_alive():
                    # Drain a late result if the child exited right after put.
                    try:
                        status, payload = result_queue.get_nowait()
                        break
                    except Empty as exc:
                        raise ProcessWorkerTerminated(
                            f"process task {task_run_id} terminated before result"
                        ) from exc
        proc.join(timeout=1.0)
        if status == "ok":
            return payload
        raise RuntimeError(f"process task failed: {payload}")
    finally:
        if proc.is_alive():
            try:
                proc.kill()
            except Exception:
                pass
            proc.join(timeout=1.0)
        reg.unregister(task_run_id, gen)
        try:
            result_queue.close()
        except Exception:
            pass


def signal_available() -> bool:
    """True when OS signals can stop child processes (not a Windows guarantee)."""
    return hasattr(signal, "SIGTERM") and hasattr(signal, "SIGKILL")
