from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.app.agents.agent_runner import AgentExecutionResult
from orchestrator.app.verification.runner import VerificationRunner


@pytest.fixture
def setup_verification_runner(tmp_path: Path):
    runner = MagicMock()
    runner.run.side_effect = [
        AgentExecutionResult(0, "3 passed", "", None),
        AgentExecutionResult(1, "", "Ruff errors found", None),
        AgentExecutionResult(0, "Formatting is correct", "", None),
    ]

    verification_runner = VerificationRunner(
        runner=runner,
        timeout_seconds=300,
    )

    return verification_runner, runner, tmp_path


def test_verification_runner_executes_all_checks(setup_verification_runner):
    verification_runner, runner, worktree = setup_verification_runner

    results = verification_runner.run_checks(worktree)

    assert len(results) == 3
    assert [result.check_name for result in results] == [
        "unit_tests",
        "ruff_check",
        "ruff_format",
    ]
    assert [result.passed for result in results] == [True, False, True]
    assert runner.run.call_count == 3


def test_verification_runner_uses_worktree_and_timeout(
    setup_verification_runner,
):
    verification_runner, runner, worktree = setup_verification_runner

    verification_runner.run_checks(worktree)

    for call in runner.run.call_args_list:
        assert call.kwargs["cwd"] == worktree
        assert call.kwargs["timeout_seconds"] == 300


def test_verification_runner_records_failure_output(
    setup_verification_runner,
):
    verification_runner, _, worktree = setup_verification_runner

    results = verification_runner.run_checks(worktree)

    assert results[1].passed is False
    assert "Ruff errors found" in results[1].details


def test_verification_runner_continues_after_failed_check(
    setup_verification_runner,
):
    verification_runner, runner, worktree = setup_verification_runner

    results = verification_runner.run_checks(worktree)

    assert results[0].passed is True
    assert results[1].passed is False
    assert results[2].passed is True
    assert runner.run.call_count == 3


def test_verification_runner_uses_current_python_and_ruff_target(tmp_path):
    import sys

    runner = MagicMock()
    runner.run.return_value = AgentExecutionResult(0, "ok", "", None)
    verification_runner = VerificationRunner(runner=runner, ruff_target="src")

    verification_runner.run_checks(tmp_path)

    commands = [call.args[0] for call in runner.run.call_args_list]
    assert all(command[0] == sys.executable for command in commands)
    assert commands[1][-1] == "src"
    assert commands[2][-2:] == ["--check", "src"]
