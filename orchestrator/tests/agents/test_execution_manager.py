from threading import Event
from time import monotonic, sleep

import pytest

from orchestrator.app.agents.execution_manager import ExecutionManager


def test_rejects_zero_workers() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        ExecutionManager(max_workers=0)


def test_rejects_duplicate_scheduled_task() -> None:
    manager = ExecutionManager(max_workers=1)
    started = Event()
    release = Event()

    def job() -> None:
        started.set()
        release.wait(timeout=2)

    try:
        manager.submit(1, job)
        assert started.wait(timeout=1)

        with pytest.raises(ValueError, match="already scheduled"):
            manager.submit(1, job)
    finally:
        release.set()
        manager.shutdown()


def test_limits_concurrent_jobs() -> None:
    manager = ExecutionManager(max_workers=1)
    started = Event()
    release = Event()
    second_started = Event()

    def first_job() -> None:
        started.set()
        release.wait(timeout=2)

    def second_job() -> None:
        second_started.set()

    try:
        manager.submit(1, first_job)
        assert started.wait(timeout=1)

        manager.submit(2, second_job)
        assert not second_started.wait(timeout=0.1)

        release.set()
        assert second_started.wait(timeout=1)
    finally:
        release.set()
        manager.shutdown()


def test_releases_task_id_after_job_finishes() -> None:
    manager = ExecutionManager(max_workers=1)
    completed = Event()

    def job() -> None:
        completed.set()

    try:
        future = manager.submit(1, job)
        future.result(timeout=2)
        assert completed.is_set()

        # Wait for the completion callback to release the task ID.
        deadline = monotonic() + 2
        while monotonic() < deadline:
            try:
                retry = manager.submit(1, job)
            except ValueError:
                sleep(0.01)
                continue

            retry.result(timeout=2)
            return

        pytest.fail("Task ID was not released after job completion.")
    finally:
        manager.shutdown()
