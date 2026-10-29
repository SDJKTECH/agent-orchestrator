# In-memory registry of running agent processes by task ID.
# Pauses, resumes and cancels them by suspending or killing the process tree.
import logging
import subprocess
import threading
import time

import psutil

logger = logging.getLogger(__name__)


class ProcessControl:
    """Track live agent processes so they can be paused, resumed or cancelled.

    State is kept in memory, so control only works inside the process that
    started the agents.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._processes: dict[int, subprocess.Popen] = {}
        self._paused: set[int] = set()
        self._cancelled: set[int] = set()

    def register(self, task_id: int, process: subprocess.Popen) -> None:
        """Track a running process, honoring control requests made earlier."""
        with self._lock:
            self._processes[task_id] = process

            if task_id in self._cancelled:
                self._kill_tree(process)
            elif task_id in self._paused:
                self._suspend_tree(process)

    def unregister(self, task_id: int) -> None:
        """Forget the task's process once it has exited."""
        with self._lock:
            self._processes.pop(task_id, None)

    def has_process(self, task_id: int) -> bool:
        """True while a process is registered for the task."""
        with self._lock:
            return task_id in self._processes

    def active_task_ids(self) -> list[int]:
        """IDs of the tasks that currently have a running process."""
        with self._lock:
            return sorted(self._processes)

    def pause(self, task_id: int) -> None:
        """Suspend the task's process, or hold the pipeline before its next stage."""
        with self._lock:
            self._paused.add(task_id)
            process = self._processes.get(task_id)
            if process is not None:
                self._suspend_tree(process)

    def resume(self, task_id: int) -> None:
        """Clear the paused flag and continue the task's suspended process."""
        with self._lock:
            self._paused.discard(task_id)
            process = self._processes.get(task_id)
            if process is not None:
                self._resume_tree(process)

    def cancel(self, task_id: int) -> None:
        """Mark the task cancelled and terminate its process if one is running."""
        with self._lock:
            self._cancelled.add(task_id)
            self._paused.discard(task_id)
            process = self._processes.get(task_id)
            if process is not None:
                self._kill_tree(process)

    def is_paused(self, task_id: int) -> bool:
        """True when the task has been asked to pause."""
        with self._lock:
            return task_id in self._paused

    def is_cancelled(self, task_id: int) -> bool:
        """True when the task has been cancelled."""
        with self._lock:
            return task_id in self._cancelled

    def clear(self, task_id: int) -> None:
        """Forget control flags so a task can be started again."""
        with self._lock:
            self._paused.discard(task_id)
            self._cancelled.discard(task_id)

    def wait_while_paused(self, task_id: int, poll_seconds: float = 0.2) -> bool:
        """Block while paused. Return False if the task was cancelled."""
        while self.is_paused(task_id) and not self.is_cancelled(task_id):
            time.sleep(poll_seconds)
        return not self.is_cancelled(task_id)

    @staticmethod
    def _tree(process: subprocess.Popen) -> list[psutil.Process]:
        """The process and all its children (empty if it already exited)."""
        try:
            parent = psutil.Process(process.pid)
            return [parent, *parent.children(recursive=True)]
        except psutil.NoSuchProcess:
            return []

    def _suspend_tree(self, process: subprocess.Popen) -> None:
        """Suspend the process and its children."""
        for item in self._tree(process):
            try:
                item.suspend()
            except psutil.NoSuchProcess:
                continue

    def _resume_tree(self, process: subprocess.Popen) -> None:
        """Continue the process and its children."""
        for item in self._tree(process):
            try:
                item.resume()
            except psutil.NoSuchProcess:
                continue

    def _kill_tree(self, process: subprocess.Popen) -> None:
        """Kill the process and its children."""
        for item in reversed(self._tree(process)):
            try:
                item.kill()
            except psutil.NoSuchProcess:
                continue
        logger.info("Agent process terminated: process_id=%s", process.pid)


process_control = ProcessControl()
