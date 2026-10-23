# Runs the verification checks: pytest, ruff check and ruff format --check.
# Each runs in a clean environment with a timeout and returns its output.
import sys
from dataclasses import dataclass
from pathlib import Path

from orchestrator.app.agents.agent_runner import (
    SECRET_ENV_VARS,
    AgentExecutionError,
    ProcessRunner,
    SubprocessRunner,
)
from orchestrator.app.core.config import Settings


def check_strip_env() -> set[str]:
    """Env vars hidden from checks: secrets and the orchestrator's own settings.

    The checked project must run in a clean environment, not the one the
    orchestrator was configured with.
    """
    return set(SECRET_ENV_VARS) | {name.upper() for name in Settings.model_fields}


@dataclass(frozen=True)
class VerificationCheckResult:
    check_name: str
    passed: bool
    details: str


class VerificationRunner:
    def __init__(
        self,
        runner: ProcessRunner | None = None,
        timeout_seconds: int = 300,
        ruff_target: str = "orchestrator",
    ) -> None:
        self.runner = runner or SubprocessRunner(strip_env=check_strip_env())
        self.timeout_seconds = timeout_seconds
        self.ruff_target = ruff_target

    def run_checks(self, worktree: Path) -> list[VerificationCheckResult]:
        checks = [
            ("unit_tests", [sys.executable, "-m", "pytest", "-v"]),
            ("ruff_check", [sys.executable, "-m", "ruff", "check", self.ruff_target]),
            (
                "ruff_format",
                [sys.executable, "-m", "ruff", "format", "--check", self.ruff_target],
            ),
        ]

        results: list[VerificationCheckResult] = []

        for check_name, command in checks:
            try:
                execution = self.runner.run(
                    command,
                    cwd=worktree,
                    timeout_seconds=self.timeout_seconds,
                )
                details = "\n".join(
                    part
                    for part in (
                        f"Command: {' '.join(command)}",
                        f"Exit code: {execution.return_code}",
                        execution.stdout,
                        execution.stderr,
                    )
                    if part
                )
                results.append(
                    VerificationCheckResult(
                        check_name=check_name,
                        passed=execution.return_code == 0,
                        details=details,
                    )
                )
            except AgentExecutionError as exc:
                results.append(
                    VerificationCheckResult(
                        check_name=check_name,
                        passed=False,
                        details=f"Command: {' '.join(command)}\nError: {exc}",
                    )
                )

        return results
