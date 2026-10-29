# Builds the Claude Code CLI command and runs it as a subprocess.
# Also strips secrets from the child environment and registers the process.
import logging
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orchestrator.app.agents.process_control import ProcessControl, process_control
from orchestrator.app.core.exceptions import AgentExecutionError

logger = logging.getLogger(__name__)

# Never hand these to agent or check processes.
SECRET_ENV_VARS = frozenset({"GITHUB_TOKEN", "GH_TOKEN"})

ALLOWED_PERMISSION_MODES = {
    "acceptEdits",
    "auto",
    "bypassPermissions",
    "manual",
    "dontAsk",
    "plan",
}


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
        task_id: int | None = None,
    ) -> AgentExecutionResult: ...


class SubprocessRunner:
    """Execute a command without invoking a shell.

    When a task ID is supplied the process is registered with the process
    control so it can be paused, resumed or cancelled while it runs.
    """

    def __init__(
        self,
        control: ProcessControl | None = None,
        strip_env: Collection[str] = SECRET_ENV_VARS,
    ) -> None:
        self.control = control or process_control
        self.strip_env = {name.upper() for name in strip_env}

    def _environment(self) -> dict[str, str]:
        """The parent environment minus secrets and any names to strip."""
        return {
            name: value
            for name, value in os.environ.items()
            if name.upper() not in self.strip_env
        }

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        task_id: int | None = None,
    ) -> AgentExecutionResult:
        """Run the command as a subprocess, stopping it after `timeout_seconds`."""
        if not cwd.is_dir():
            raise AgentExecutionError(f"Agent working directory does not exist: {cwd}")

        process: subprocess.Popen[str] | None = None

        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=self._environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )

            process_id = process.pid

            if task_id is not None:
                self.control.register(task_id, process)

            try:
                stdout, stderr = process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                stdout, stderr = process.communicate()

                raise AgentExecutionError(
                    f"Agent execution exceeded {timeout_seconds} seconds."
                ) from exc
            finally:
                if task_id is not None:
                    self.control.unregister(task_id)

        except AgentExecutionError:
            raise
        except OSError as exc:
            raise AgentExecutionError(f"Could not start agent process: {exc}") from exc

        result = AgentExecutionResult(
            return_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            process_id=process_id,
        )

        if result.return_code != 0:
            logger.error(
                "Agent process failed: return_code=%s process_id=%s",
                result.return_code,
                result.process_id,
            )

        return result


def resolve_executable(command: str) -> str:
    """Resolve a bare command name to something Popen can start without a shell.

    On Windows the npm `claude` launcher is a .cmd shim that cannot be started
    directly (and mangles multi-line arguments), so use the native claude.exe
    that ships beside it. Anything that is not a bare name is returned as is.
    """
    if not re.fullmatch(r"[\w.-]+", command):
        return command

    found = shutil.which(command)

    if found is None:
        return command

    path = Path(found)

    if path.suffix.lower() in {".cmd", ".bat"} and path.stem.lower() == "claude":
        native = (
            path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin"
        ) / "claude.exe"
        if native.is_file():
            return str(native)

    return found


def split_claude_command(claude_command: str) -> list[str]:
    """Split a configured command into argv, keeping an executable path whole."""
    if Path(claude_command).exists() or shutil.which(claude_command):
        return [claude_command]

    tokens = shlex.split(claude_command, posix=os.name != "nt")
    return [token.strip("\"'") for token in tokens] or [claude_command]


def build_claude_command(
    *,
    claude_command: str,
    prompt: str,
    model: str | None = None,
    effort: str | None = None,
    max_budget_usd: float | None = None,
    agent: str | None = None,
    permission_mode: str | None = None,
    allowed_tools: list[str] | None = None,
) -> list[str]:
    """Build a non-interactive Claude Code CLI command."""
    if not prompt.strip():
        raise ValueError("Agent prompt cannot be empty.")

    command = [
        *split_claude_command(claude_command),
        "--print",
        "--output-format",
        "json",
    ]

    if agent:
        if not agent.strip():
            raise ValueError("Agent name cannot be empty.")
        command.extend(["--agent", agent])

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

    if permission_mode:
        if permission_mode not in ALLOWED_PERMISSION_MODES:
            raise ValueError(f"Unsupported permission mode: {permission_mode}")
        command.extend(["--permission-mode", permission_mode])

    if allowed_tools:
        # --allowedTools accepts many values, so end the options with "--"
        # to keep the prompt from being read as another tool name.
        command.extend(["--allowedTools", ",".join(allowed_tools), "--"])

    command.append(prompt)
    return command
