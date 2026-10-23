from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import TypeVar

T = TypeVar("T")


class ExecutionManager:
    """Schedule agent jobs with a configurable concurrency limit."""

    def __init__(self, max_workers: int) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1.")

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="agent-worker",
        )
        self._lock = Lock()
        self._scheduled_task_ids: set[int] = set()

    def submit(self, task_id: int, job: Callable[[], T]) -> Future[T]:
        """Schedule a task, rejecting duplicate submissions."""
        with self._lock:
            if task_id in self._scheduled_task_ids:
                raise ValueError(f"Task {task_id} is already scheduled or running.")

            self._scheduled_task_ids.add(task_id)

            try:
                future = self._executor.submit(
                    self._run_and_release,
                    task_id,
                    job,
                )
            except Exception:
                self._scheduled_task_ids.discard(task_id)
                raise

            return future

    def _run_and_release(self, task_id: int, job: Callable[[], T]) -> T:
        """Run a job and release its task ID even if it raises."""
        try:
            return job()
        finally:
            self._release_task(task_id)

    def _release_task(self, task_id: int) -> None:
        with self._lock:
            self._scheduled_task_ids.discard(task_id)

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop accepting jobs and optionally wait for submitted work."""
        self._executor.shutdown(wait=wait)
