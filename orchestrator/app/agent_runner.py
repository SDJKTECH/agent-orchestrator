import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orchestrator.app.exceptions import AgentExecutionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentExecutionResult:
    """Result of a completed agent process."""

    return_code: int
    stdout: str
    stderr: str
    process_id: int | None


class ProcessRunner(Protocol):
    """Interface that allows process execution to be replaced in tests."""

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
    ) -> AgentExecutionResult: ...


class SubprocessRunner:
    """Execute a command without invoking a shell."""

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
    ) -> AgentExecutionResult:
        if not cwd.is_dir():
            raise AgentExecutionError(f"Agent working directory does not exist: {cwd}")

        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentExecutionError(
                f"Agent execution exceeded {timeout_seconds} seconds."
            ) from exc
        except OSError as exc:
            raise AgentExecutionError(f"Could not start agent process: {exc}") from exc

        result = AgentExecutionResult(
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            process_id=None,
        )

        if result.return_code != 0:
            logger.error(
                "Agent process failed with exit code %s. stderr: %s",
                result.return_code,
                result.stderr[-4000:],
            )

        return result


def build_claude_command(
    *,
    claude_command: str,
    prompt: str,
    model: str | None = None,
    effort: str | None = None,
    max_budget_usd: float | None = None,
) -> list[str]:
    """Build a non-interactive Claude Code CLI command."""
    if not prompt.strip():
        raise ValueError("Agent prompt cannot be empty.")

    command = [claude_command, "--print", "--output-format", "json"]

    if model:
        command.extend(["--model", model])

    if effort:
        allowed_efforts = {"low", "medium", "high", "xhigh", "max"}
        if effort not in allowed_efforts:
            raise ValueError(f"Unsupported effort level: {effort}")
        command.extend(["--effort", effort])

    if max_budget_usd is not None:
        if max_budget_usd <= 0:
            raise ValueError("max_budget_usd must be greater than zero.")
        command.extend(["--max-budget-usd", str(max_budget_usd)])

    command.append(prompt)
    return command
