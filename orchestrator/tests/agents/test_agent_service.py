import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orchestrator.app.agents.agent_runner import AgentExecutionResult
from orchestrator.app.agents.agent_service import MAX_RESULT_CHARS, AgentService
from orchestrator.app.core.exceptions import AgentExecutionError
from orchestrator.app.db.models import AgentRun, Task, TaskStatus


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
        verification_timeout_seconds=300,
        default_model="sonnet",
        default_effort="medium",
    )
    monkeypatch.setattr(
        "orchestrator.app.agents.agent_service.get_settings",
        lambda: settings,
    )

    verification_runner = MagicMock()
    verification_runner.run_checks.return_value = [
        SimpleNamespace(
            check_name="unit_tests",
            passed=True,
            details="All tests passed",
        ),
        SimpleNamespace(
            check_name="ruff_check",
            passed=True,
            details="All checks passed",
        ),
        SimpleNamespace(
            check_name="ruff_format",
            passed=True,
            details="Formatting is correct",
        ),
    ]

    service = AgentService(
        db=db,
        runner=runner,
        verification_runner=verification_runner,
    )

    return SimpleNamespace(
        service=service,
        db=db,
        runner=runner,
        verification_runner=verification_runner,
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
    context.verification_runner.run_checks.assert_called_once_with(context.worktree)


def test_run_task_logs_execution_lifecycle(setup_agent_service, caplog):
    context = setup_agent_service

    with caplog.at_level("INFO", logger="orchestrator.app.agents.agent_service"):
        context.service.run_task(1)

    log_output = caplog.text

    assert "Agent execution started:" in log_output
    assert "task_id=1" in log_output
    assert "role=builder" in log_output
    assert "model=sonnet" in log_output
    assert "effort=medium" in log_output
    assert "Agent execution finished:" in log_output
    assert "status=COMPLETED" in log_output
    assert "return_code=0" in log_output
    assert "duration_seconds=" in log_output


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
    context.verification_runner.run_checks.assert_not_called()


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


def test_run_task_keeps_the_final_message_exit_code_and_turns(setup_agent_service):
    context = setup_agent_service
    context.runner.run.return_value = AgentExecutionResult(
        return_code=0,
        stdout='{"result": "Added the parser.", "num_turns": 9}',
        stderr="",
        process_id=1,
    )

    result = context.service.run_task(1)

    assert result.result_text == "Added the parser."
    assert result.num_turns == 9
    assert result.return_code == 0


def test_run_task_cuts_a_very_long_final_message(setup_agent_service):
    context = setup_agent_service
    context.runner.run.return_value = AgentExecutionResult(
        return_code=0,
        stdout=json.dumps({"result": "x" * 50000}),
        stderr="",
        process_id=1,
    )

    result = context.service.run_task(1)

    assert len(result.result_text) == MAX_RESULT_CHARS


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


def test_run_task_marks_task_failed_when_verification_fails(
    setup_agent_service,
):
    context = setup_agent_service
    context.verification_runner.run_checks.return_value = [
        SimpleNamespace(
            check_name="unit_tests",
            passed=True,
            details="All tests passed",
        ),
        SimpleNamespace(
            check_name="ruff_check",
            passed=False,
            details="Ruff found errors",
        ),
        SimpleNamespace(
            check_name="ruff_format",
            passed=True,
            details="Formatting is correct",
        ),
    ]

    result = context.service.run_task(1)

    assert context.task.status == TaskStatus.FAILED
    assert result.status == "FAILED"
    context.verification_runner.run_checks.assert_called_once_with(context.worktree)
    assert context.db.add.call_count == 4


def test_run_task_marks_task_failed_when_no_verification_results(
    setup_agent_service,
):
    context = setup_agent_service
    context.verification_runner.run_checks.return_value = []

    result = context.service.run_task(1)

    assert context.task.status == TaskStatus.FAILED
    assert result.status == "FAILED"
    context.verification_runner.run_checks.assert_called_once_with(context.worktree)


def test_run_task_passes_task_id_to_runner(setup_agent_service):
    context = setup_agent_service

    context.service.run_task(1)

    assert context.runner.run.call_args.kwargs["task_id"] == 1


def test_run_task_marks_cancelled_when_task_was_cancelled(setup_agent_service):
    from orchestrator.app.agents.process_control import ProcessControl

    context = setup_agent_service
    control = ProcessControl()
    context.service.control = control
    control.cancel(1)
    context.runner.run.return_value = AgentExecutionResult(
        return_code=1, stdout="", stderr="", process_id=7
    )

    result = context.service.run_task(1)

    assert result.status == "CANCELLED"
    assert context.task.status == TaskStatus.CANCELLED
    assert result.finished_at is not None
    context.verification_runner.run_checks.assert_not_called()


def test_run_task_passes_permissions_to_the_agent(setup_agent_service):
    context = setup_agent_service
    context.service.settings.claude_permission_mode = "acceptEdits"
    context.service.settings.claude_allowed_tools = "Bash(git status:*), Edit"

    context.service.run_task(1)

    command = context.runner.run.call_args.args[0]
    assert command[command.index("--permission-mode") + 1] == "acceptEdits"
    assert command[command.index("--allowedTools") + 1] == "Bash(git status:*),Edit"
    assert command[-2] == "--"
