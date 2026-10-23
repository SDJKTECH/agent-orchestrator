import subprocess
import sys
import threading
import time

import psutil
import pytest

from orchestrator.app.agent_runner import SubprocessRunner
from orchestrator.app.process_control import ProcessControl


@pytest.fixture
def sleeper():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def wait_for_status(process: subprocess.Popen, expected: str) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if psutil.Process(process.pid).status() == expected:
            return True
        time.sleep(0.05)
    return False


def test_pause_suspends_and_resume_restarts_process(sleeper) -> None:
    control = ProcessControl()
    control.register(1, sleeper)

    control.pause(1)

    assert control.is_paused(1)
    assert wait_for_status(sleeper, psutil.STATUS_STOPPED)

    control.resume(1)

    assert not control.is_paused(1)
    assert wait_for_status(sleeper, psutil.STATUS_RUNNING)


def test_cancel_kills_process_and_marks_task(sleeper) -> None:
    control = ProcessControl()
    control.register(1, sleeper)

    control.cancel(1)

    assert control.is_cancelled(1)
    assert sleeper.wait(timeout=5) != 0


def test_cancel_before_registration_kills_new_process(sleeper) -> None:
    control = ProcessControl()
    control.cancel(7)

    control.register(7, sleeper)

    assert sleeper.wait(timeout=5) != 0


def test_unregister_and_clear_reset_state(sleeper) -> None:
    control = ProcessControl()
    control.register(1, sleeper)
    assert control.active_task_ids() == [1]
    assert control.has_process(1)

    control.unregister(1)
    control.pause(1)
    control.cancel(1)
    control.clear(1)

    assert control.active_task_ids() == []
    assert not control.is_paused(1)
    assert not control.is_cancelled(1)


def test_wait_while_paused_returns_after_resume() -> None:
    control = ProcessControl()
    control.pause(1)
    threading.Timer(0.2, control.resume, args=(1,)).start()

    assert control.wait_while_paused(1, poll_seconds=0.02) is True


def test_wait_while_paused_returns_false_when_cancelled() -> None:
    control = ProcessControl()
    control.pause(1)
    threading.Timer(0.2, control.cancel, args=(1,)).start()

    assert control.wait_while_paused(1, poll_seconds=0.02) is False


def test_subprocess_runner_registers_process_for_task(tmp_path) -> None:
    control = ProcessControl()
    runner = SubprocessRunner(control=control)
    seen: list[list[int]] = []

    def observe() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            active = control.active_task_ids()
            if active:
                seen.append(active)
                return
            time.sleep(0.02)

    watcher = threading.Thread(target=observe)
    watcher.start()

    result = runner.run(
        [sys.executable, "-c", "import time; time.sleep(0.5)"],
        cwd=tmp_path,
        timeout_seconds=30,
        task_id=5,
    )
    watcher.join()

    assert result.return_code == 0
    assert seen == [[5]]
    assert control.active_task_ids() == []


def test_subprocess_runner_returns_when_process_is_cancelled(tmp_path) -> None:
    control = ProcessControl()
    runner = SubprocessRunner(control=control)
    threading.Timer(0.5, control.cancel, args=(9,)).start()

    started = time.monotonic()
    result = runner.run(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        timeout_seconds=60,
        task_id=9,
    )

    assert result.return_code != 0
    assert time.monotonic() - started < 30
