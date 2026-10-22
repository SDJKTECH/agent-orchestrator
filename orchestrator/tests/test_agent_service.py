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

    agent_definition = worktree / ".claude" / "agents" / "builder.md"
    agent_definition.parent.mkdir(parents=True)
    agent_definition.write_text(
        "---\nname: builder\ndescription: Test builder agent\n---\n",
        encoding="utf-8",
    )

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
        default_model="sonnet",
        default_effort="medium",
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


def test_run_task_rejects_missing_builder_definition(setup_agent_service):
    context = setup_agent_service
    agent_definition = context.worktree / ".claude" / "agents" / "builder.md"
    agent_definition.unlink()

    with pytest.raises(AgentExecutionError, match="Builder agent definition"):
        context.service.run_task(1)

    context.runner.run.assert_not_called()


def test_run_task_persists_process_id(setup_agent_service):
    context = setup_agent_service

    context.runner.run.return_value = AgentExecutionResult(
        return_code=0,
        stdout='{"result": "Task completed"}',
        stderr="",
        process_id=12345,
    )

    result = context.service.run_task(1)

    assert result.process_id == 12345
    assert context.task.status == TaskStatus.COMPLETED


def test_run_task_allows_missing_process_id(setup_agent_service):
    context = setup_agent_service

    context.runner.run.return_value = AgentExecutionResult(
        return_code=0,
        stdout='{"result": "Task completed"}',
        stderr="",
        process_id=None,
    )

    result = context.service.run_task(1)

    assert result.process_id is None
    assert context.task.status == TaskStatus.COMPLETED


def test_run_task_uses_default_model_and_effort(setup_agent_service):
    context = setup_agent_service

    result = context.service.run_task(1)

    command = context.runner.run.call_args.args[0]

    assert command[command.index("--model") + 1] == "sonnet"
    assert command[command.index("--effort") + 1] == "medium"
    assert result.model == "sonnet"
    assert result.effort == "medium"


def test_run_task_uses_explicit_model_and_effort(setup_agent_service):
    context = setup_agent_service

    result = context.service.run_task(
        1,
        model="opus",
        effort="high",
    )

    command = context.runner.run.call_args.args[0]

    assert command[command.index("--model") + 1] == "opus"
    assert command[command.index("--effort") + 1] == "high"
    assert result.model == "opus"
    assert result.effort == "high"


def test_run_task_rejects_invalid_effort_before_execution(setup_agent_service):
    context = setup_agent_service

    with pytest.raises(ValueError, match="Unsupported effort level"):
        context.service.run_task(1, effort="extreme")

    context.runner.run.assert_not_called()
    context.db.add.assert_not_called()


def test_run_task_persists_usage_metrics(setup_agent_service):
    context = setup_agent_service
    context.runner.run.return_value = AgentExecutionResult(
        return_code=0,
        stdout=(
            '{"session_id": "session-123", '
            '"total_cost_usd": 0.0379284, '
            '"usage": {'
            '"input_tokens": 2, '
            '"output_tokens": 4, '
            '"cache_creation_input_tokens": 9117, '
            '"cache_read_input_tokens": 14164'
            "}, "
            '"modelUsage": {'
            '"claude-sonnet-5-5": {'
            '"inputTokens": 2, '
            '"outputTokens": 4, '
            '"cacheReadInputTokens": 14164, '
            '"cacheCreationInputTokens": 9117, '
            '"costUSD": 0.0379284'
            "}"
            "}}"
        ),
        stderr="",
        process_id=12345,
    )

    result = context.service.run_task(1)

    assert result.input_tokens == 2
    assert result.output_tokens == 4
    assert result.cache_creation_input_tokens == 9117
    assert result.cache_read_input_tokens == 14164
    assert result.reported_cost_usd == pytest.approx(0.0379284)
    assert result.session_id == "session-123"

    # Preserve the configured model alias.
    assert result.model == "sonnet"
    assert result.status == "COMPLETED"


def test_run_task_handles_missing_usage_metrics(setup_agent_service):
    context = setup_agent_service

    result = context.service.run_task(1)

    assert result.input_tokens in (None, 0)
    assert result.output_tokens in (None, 0)
    assert result.cache_creation_input_tokens is None
    assert result.cache_read_input_tokens is None
    assert result.reported_cost_usd is None
    assert result.session_id is None
    assert result.status == "COMPLETED"


def test_run_task_persists_usage_when_execution_fails(setup_agent_service):
    context = setup_agent_service
    context.runner.run.return_value = AgentExecutionResult(
        return_code=1,
        stdout=(
            '{"session_id": "session-failed", '
            '"total_cost_usd": 0.01, '
            '"usage": {"input_tokens": 10, "output_tokens": 3}}'
        ),
        stderr="Task failed",
        process_id=12345,
    )

    result = context.service.run_task(1)

    assert result.status == "FAILED"
    assert result.input_tokens == 10
    assert result.output_tokens == 3
    assert result.reported_cost_usd == pytest.approx(0.01)
    assert result.session_id == "session-failed"
