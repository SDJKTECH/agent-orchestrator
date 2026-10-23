import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.app.agents.agent_runner import (
    AgentExecutionResult,
    SubprocessRunner,
    build_claude_command,
)
from orchestrator.app.core.exceptions import AgentExecutionError


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

    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.returncode = 0
    mock_process.communicate.return_value = (
        "Agent completed",
        "",
    )

    with patch(
        "orchestrator.app.agents.agent_runner.subprocess.Popen",
        return_value=mock_process,
    ) as mock_popen:
        result = runner.run(
            ["fake-command"],
            cwd=tmp_path,
            timeout_seconds=30,
        )

    assert isinstance(result, AgentExecutionResult)
    assert result.return_code == 0
    assert result.stdout == "Agent completed"
    assert result.stderr == ""
    assert result.process_id == 12345

    mock_popen.assert_called_once()
    assert mock_popen.call_args.kwargs["shell"] is False
    mock_process.communicate.assert_called_once_with(timeout=30)


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

    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.communicate.side_effect = [
        subprocess.TimeoutExpired(
            cmd=["fake-command"],
            timeout=30,
        ),
        ("", ""),
    ]

    with patch(
        "orchestrator.app.agents.agent_runner.subprocess.Popen",
        return_value=mock_process,
    ):
        with pytest.raises(AgentExecutionError, match="exceeded"):
            runner.run(
                ["fake-command"],
                cwd=tmp_path,
                timeout_seconds=30,
            )

    mock_process.kill.assert_called_once()
    assert mock_process.communicate.call_count == 2


def test_subprocess_runner_handles_startup_error(
    tmp_path: Path,
) -> None:
    runner = SubprocessRunner()

    with patch(
        "orchestrator.app.agents.agent_runner.subprocess.run",
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


def test_subprocess_runner_does_not_log_stderr(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = SubprocessRunner()
    sensitive_stderr = "Sensitive diagnostic output"

    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.returncode = 1
    mock_process.communicate.return_value = (
        "",
        sensitive_stderr,
    )

    with patch(
        "orchestrator.app.agents.agent_runner.subprocess.Popen",
        return_value=mock_process,
    ):
        with caplog.at_level(
            "ERROR",
            logger="orchestrator.app.agents.agent_runner",
        ):
            result = runner.run(
                ["fake-command"],
                cwd=tmp_path,
                timeout_seconds=30,
            )

    assert result.return_code == 1
    assert result.stderr == sensitive_stderr
    assert "Agent process failed" in caplog.text
    assert "return_code=1" in caplog.text
    assert "process_id=12345" in caplog.text
    assert sensitive_stderr not in caplog.text


def test_split_claude_command_keeps_plain_name_and_existing_path(
    tmp_path: Path,
) -> None:
    from orchestrator.app.agents.agent_runner import split_claude_command

    executable = tmp_path / "dir with spaces" / "claude.exe"
    executable.parent.mkdir()
    executable.write_text("", encoding="utf-8")

    assert split_claude_command("claude") == ["claude"]
    assert split_claude_command(str(executable)) == [str(executable)]


def test_split_claude_command_splits_quoted_command_with_arguments() -> None:
    from orchestrator.app.agents.agent_runner import split_claude_command

    command = split_claude_command('"/opt/my python/python" "/opt/stub dir/fake.py"')

    assert command == ["/opt/my python/python", "/opt/stub dir/fake.py"]


def test_build_claude_command_expands_multi_token_command() -> None:
    command = build_claude_command(
        claude_command='"/opt/my python/python" "/opt/fake.py"',
        prompt="Do the task",
    )

    assert command[:3] == ["/opt/my python/python", "/opt/fake.py", "--print"]
    assert command[-1] == "Do the task"


def test_resolve_executable_prefers_native_claude_over_npm_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.app.agents.agent_runner import resolve_executable

    shim = tmp_path / "claude.CMD"
    shim.write_text("", encoding="utf-8")
    native = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin"
    native.mkdir(parents=True)
    (native / "claude.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda name: str(shim))

    assert resolve_executable("claude") == str(native / "claude.exe")


def test_resolve_executable_keeps_shim_without_native_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.app.agents.agent_runner import resolve_executable

    shim = tmp_path / "claude.CMD"
    shim.write_text("", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda name: str(shim))

    assert resolve_executable("claude") == str(shim)


def test_resolve_executable_leaves_unknown_and_complex_commands_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.app.agents.agent_runner import resolve_executable

    monkeypatch.setattr("shutil.which", lambda name: None)

    assert resolve_executable("claude") == "claude"
    assert resolve_executable('"/opt/my python/python" "/opt/fake.py"') == (
        '"/opt/my python/python" "/opt/fake.py"'
    )


def test_build_claude_command_adds_permissions_before_the_prompt() -> None:
    command = build_claude_command(
        claude_command="claude",
        prompt="Do the task",
        permission_mode="acceptEdits",
        allowed_tools=["Bash(git status:*)", "Bash(python -m pytest:*)"],
    )

    assert command[command.index("--permission-mode") + 1] == "acceptEdits"
    tools = command[command.index("--allowedTools") + 1]
    assert tools == "Bash(git status:*),Bash(python -m pytest:*)"
    # "--" must separate the variadic tool list from the prompt.
    assert command[-2:] == ["--", "Do the task"]


def test_build_claude_command_rejects_unknown_permission_mode() -> None:
    with pytest.raises(ValueError, match="permission mode"):
        build_claude_command(
            claude_command="claude",
            prompt="Do the task",
            permission_mode="yolo",
        )


def test_subprocess_runner_hides_secrets_from_child_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setenv("GITHUB_TOKEN", "secret-value")
    monkeypatch.setenv("KEEP_ME", "visible")
    script = (
        "import os; print(os.environ.get('GITHUB_TOKEN', 'absent'),"
        " os.environ.get('KEEP_ME', 'absent'))"
    )

    result = SubprocessRunner().run(
        [sys.executable, "-c", script], cwd=tmp_path, timeout_seconds=30
    )

    assert result.stdout.split() == ["absent", "visible"]


def test_verification_checks_run_without_orchestrator_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    from orchestrator.app.verification.runner import check_strip_env

    monkeypatch.setenv("MAX_CONCURRENT_AGENTS", "1")
    monkeypatch.setenv("REPOSITORY_PATH", "/somewhere")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-value")
    script = (
        "import os; print(*(n for n in ('MAX_CONCURRENT_AGENTS',"
        " 'REPOSITORY_PATH', 'GITHUB_TOKEN') if n in os.environ))"
    )

    result = SubprocessRunner(strip_env=check_strip_env()).run(
        [sys.executable, "-c", script], cwd=tmp_path, timeout_seconds=30
    )

    assert result.stdout.strip() == ""
