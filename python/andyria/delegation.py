"""Delegation — sub-agent spawning, mirrors hermes-agent's delegate_task tool.

Allows an agent to spin up isolated Andyria coordinator instances
(in separate threads) to execute sub-tasks in parallel, then collect
their results.

Usage::

    dm = DelegationManager(coordinator_factory=make_coordinator)
    task_id = dm.spawn("Summarise the last 50 log lines", tools=["read_file"])
    result  = dm.collect(task_id, timeout=30)
    results = dm.collect_all([id1, id2], timeout=60)
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class DelegateTask:
    id: str
    prompt: str
    tools: List[str]
    config: Dict[str, Any]
    spawned_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None


class DelegationManager:
    """Manages a pool of delegated sub-agent tasks.

    Args:
        coordinator_factory:  Callable ``(prompt, tools, config) → str`` that
                              runs a task in isolation and returns the result.
                              In production this calls ``Coordinator.process()``.
        max_workers:          Thread pool size (default 4).
    """

    def __init__(
        self,
        coordinator_factory: Callable[[str, List[str], Dict[str, Any]], str],
        max_workers: int = 4,
    ) -> None:
        self._factory = coordinator_factory
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="andyria-delegate")
        self._tasks: Dict[str, DelegateTask] = {}
        self._futures: Dict[str, Future] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def spawn(
        self,
        prompt: str,
        tools: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Spawn a sub-agent task asynchronously. Returns the task ID."""
        task_id = str(uuid.uuid4())[:10]
        task = DelegateTask(
            id=task_id,
            prompt=prompt,
            tools=tools or [],
            config=config or {},
        )
        with self._lock:
            self._tasks[task_id] = task
            future = self._executor.submit(self._run, task)
            self._futures[task_id] = future
        return task_id

    def collect(self, task_id: str, timeout: float = 60.0) -> Optional[DelegateTask]:
        """Block until a task completes (or times out). Returns the task."""
        with self._lock:
            future = self._futures.get(task_id)
        if future is None:
            return None
        try:
            future.result(timeout=timeout)
        except Exception:
            pass
        with self._lock:
            return self._tasks.get(task_id)

    def collect_all(self, task_ids: List[str], timeout: float = 120.0) -> List[DelegateTask]:
        """Wait for all specified tasks. Returns list of completed tasks."""
        deadline = time.time() + timeout
        results = []
        for task_id in task_ids:
            remaining = max(0.1, deadline - time.time())
            task = self.collect(task_id, timeout=remaining)
            if task:
                results.append(task)
        return results

    def status(self, task_id: str) -> Optional[dict]:
        """Return a status snapshot of a task."""
        with self._lock:
            task = self._tasks.get(task_id)
            future = self._futures.get(task_id)
        if task is None:
            return None
        return {
            "id": task.id,
            "prompt_preview": task.prompt[:80],
            "spawned_at": task.spawned_at,
            "finished": future.done() if future else False,
            "result_preview": (task.result or "")[:80] if task.result else None,
            "error": task.error,
        }

    def list_tasks(self) -> List[dict]:
        with self._lock:
            items = list(self._tasks.values())
        return [
            {
                "id": t.id,
                "prompt_preview": t.prompt[:60],
                "status": "done" if t.finished_at else "running",
                "result_preview": (t.result or "")[:60] if t.result else None,
                "error": t.error,
            }
            for t in items
        ]

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self, task: DelegateTask) -> None:
        try:
            result = self._factory(task.prompt, task.tools, task.config)
            with self._lock:
                task.result = result
                task.finished_at = time.time()
        except Exception as exc:
            with self._lock:
                task.error = str(exc)
                task.finished_at = time.time()
