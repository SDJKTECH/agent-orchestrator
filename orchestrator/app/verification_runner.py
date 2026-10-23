from dataclasses import dataclass
from pathlib import Path

from orchestrator.app.agent_runner import (
    AgentExecutionError,
    ProcessRunner,
    SubprocessRunner,
)


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
    ) -> None:
        self.runner = runner or SubprocessRunner()
        self.timeout_seconds = timeout_seconds

    def run_checks(self, worktree: Path) -> list[VerificationCheckResult]:
        checks = [
            ("unit_tests", ["python", "-m", "pytest", "-v"]),
            ("ruff_check", ["python", "-m", "ruff", "check", "orchestrator"]),
            (
                "ruff_format",
                ["python", "-m", "ruff", "format", "--check", "orchestrator"],
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
