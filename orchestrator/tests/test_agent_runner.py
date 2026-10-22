import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.app.agent_runner import (
    AgentExecutionResult,
    SubprocessRunner,
    build_claude_command,
)
from orchestrator.app.exceptions import AgentExecutionError


def test_build_claude_command_defaults() -> None:
    command = build_claude_command(
        claude_command="claude",
        prompt="Implement a health endpoint",
    )

    assert command == [
        "claude",
        "--print",
        "--output-format",
        "json",
        "Implement a health endpoint",
    ]


def test_build_claude_command_with_options() -> None:
    command = build_claude_command(
        claude_command="claude",
        prompt="Fix failing tests",
        model="sonnet",
        effort="high",
        max_budget_usd=2.5,
    )

    assert "--model" in command
    assert command[command.index("--model") + 1] == "sonnet"
    assert "--effort" in command
    assert command[command.index("--effort") + 1] == "high"
    assert "--max-budget-usd" in command
    assert command[command.index("--max-budget-usd") + 1] == "2.5"


def test_build_claude_command_rejects_empty_prompt() -> None:
    with pytest.raises(ValueError, match="prompt"):
        build_claude_command(
            claude_command="claude",
            prompt="   ",
        )


def test_build_claude_command_rejects_invalid_effort() -> None:
    with pytest.raises(ValueError, match="effort"):
        build_claude_command(
            claude_command="claude",
            prompt="Test task",
            effort="invalid",
        )


def test_build_claude_command_rejects_invalid_budget() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        build_claude_command(
            claude_command="claude",
            prompt="Test task",
            max_budget_usd=0,
        )


def test_subprocess_runner_returns_output(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    working_directory = tmp_path

    with patch("orchestrator.app.agent_runner.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["fake-command"],
            returncode=0,
            stdout="Agent completed",
            stderr="",
        )

        result = runner.run(
            ["fake-command"],
            cwd=working_directory,
            timeout_seconds=30,
        )

    assert isinstance(result, AgentExecutionResult)
    assert result.return_code == 0
    assert result.stdout == "Agent completed"
    assert result.stderr == ""

    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["shell"] is False
    assert mock_run.call_args.kwargs["timeout"] == 30


def test_subprocess_runner_rejects_missing_directory(
    tmp_path: Path,
) -> None:
    runner = SubprocessRunner()
    missing_directory = tmp_path / "missing"

    with pytest.raises(AgentExecutionError, match="does not exist"):
        runner.run(
            ["fake-command"],
            cwd=missing_directory,
            timeout_seconds=30,
        )


def test_subprocess_runner_handles_timeout(tmp_path: Path) -> None:
    runner = SubprocessRunner()

    with patch(
        "orchestrator.app.agent_runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(
            cmd=["fake-command"],
            timeout=30,
        ),
    ):
        with pytest.raises(AgentExecutionError, match="exceeded"):
            runner.run(
                ["fake-command"],
                cwd=tmp_path,
                timeout_seconds=30,
            )


def test_subprocess_runner_handles_startup_error(
    tmp_path: Path,
) -> None:
    runner = SubprocessRunner()

    with patch(
        "orchestrator.app.agent_runner.subprocess.run",
        side_effect=OSError("Executable not found"),
    ):
        with pytest.raises(AgentExecutionError, match="Could not start"):
            runner.run(
                ["fake-command"],
                cwd=tmp_path,
                timeout_seconds=30,
            )


def test_build_claude_command_with_agent() -> None:
    command = build_claude_command(
        claude_command="claude",
        prompt="Implement a health endpoint",
        agent="builder",
    )

    assert "--agent" in command
    assert command[command.index("--agent") + 1] == "builder"
    assert command[-1] == "Implement a health endpoint"


def test_build_claude_command_rejects_empty_agent() -> None:
    with pytest.raises(ValueError, match="Agent name"):
        build_claude_command(
            claude_command="claude",
            prompt="Test task",
            agent="   ",
        )
