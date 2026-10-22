from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orchestrator.app.agent_runner import AgentExecutionResult
from orchestrator.app.agent_service import AgentService
from orchestrator.app.exceptions import AgentExecutionError
from orchestrator.app.models import AgentRun, Task, TaskStatus


@pytest.fixture
def setup_agent_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create a task and service with a mocked process runner."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    task = Task(
        id=1,
        title="Add a health endpoint",
        description="Implement a health-check endpoint.",
        status=TaskStatus.QUEUED,
        worktree_path=str(worktree),
    )

    db = MagicMock()
    db.get.return_value = task

    runner = MagicMock()
    runner.run.return_value = AgentExecutionResult(
        return_code=0,
        stdout='{"result": "Task completed"}',
        stderr="",
        process_id=None,
    )

    settings = SimpleNamespace(
        claude_command="claude",
        max_task_cost_usd=1.0,
        agent_timeout_seconds=60,
    )
    monkeypatch.setattr(
        "orchestrator.app.agent_service.get_settings",
        lambda: settings,
    )

    service = AgentService(db=db, runner=runner)

    return SimpleNamespace(
        service=service,
        db=db,
        runner=runner,
        task=task,
        worktree=worktree,
    )


def test_run_task_completes_successfully(setup_agent_service):
    context = setup_agent_service

    result = context.service.run_task(1)

    assert isinstance(result, AgentRun)
    assert context.task.status == TaskStatus.COMPLETED
    assert result.status == "COMPLETED"
    assert result.finished_at is not None
    context.runner.run.assert_called_once()


def test_run_task_marks_task_failed_on_nonzero_exit(setup_agent_service):
    context = setup_agent_service
    context.runner.run.return_value = AgentExecutionResult(
        return_code=1,
        stdout="",
        stderr="Something went wrong",
        process_id=None,
    )

    result = context.service.run_task(1)

    assert context.task.status == TaskStatus.FAILED
    assert result.status == "FAILED"
    assert result.finished_at is not None


def test_run_task_rejects_missing_task(setup_agent_service):
    context = setup_agent_service
    context.db.get.return_value = None

    with pytest.raises(ValueError, match="does not exist"):
        context.service.run_task(999)

    context.runner.run.assert_not_called()


def test_run_task_rejects_invalid_status(setup_agent_service):
    context = setup_agent_service
    context.task.status = TaskStatus.COMPLETED

    with pytest.raises(ValueError, match="cannot run"):
        context.service.run_task(1)

    context.runner.run.assert_not_called()


def test_run_task_rejects_missing_worktree_path(setup_agent_service):
    context = setup_agent_service
    context.task.worktree_path = None

    with pytest.raises(AgentExecutionError, match="worktree"):
        context.service.run_task(1)

    context.runner.run.assert_not_called()


def test_run_task_rejects_nonexistent_worktree(setup_agent_service):
    context = setup_agent_service
    context.task.worktree_path = str(context.worktree / "missing")

    with pytest.raises(AgentExecutionError, match="does not exist"):
        context.service.run_task(1)

    context.runner.run.assert_not_called()


def test_run_task_marks_task_failed_on_execution_error(setup_agent_service):
    context = setup_agent_service
    context.runner.run.side_effect = AgentExecutionError("Process timed out")

    with pytest.raises(AgentExecutionError, match="timed out"):
        context.service.run_task(1)

    assert context.task.status == TaskStatus.FAILED
    assert context.db.commit.call_count >= 2


def test_run_task_uses_builder_agent(setup_agent_service):
    context = setup_agent_service

    result = context.service.run_task(1)

    command = context.runner.run.call_args.args[0]

    assert "--agent" in command
    assert command[command.index("--agent") + 1] == "builder"
    assert result.agent_role == "builder"
