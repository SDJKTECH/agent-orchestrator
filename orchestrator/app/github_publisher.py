import logging
import subprocess
from pathlib import Path
from typing import Protocol

from orchestrator.app.models import Task

logger = logging.getLogger(__name__)


class PullRequestPublisher(Protocol):
    def open_pull_request(self, task: Task, head: str) -> str | None: ...


def pull_request_body(task: Task) -> str:
    lines = [
        "Automated change produced by the agent orchestrator "
        f"(task {task.id}); the reviewer agent approved it.",
        "",
        "Branches: "
        f"`agent/task-{task.id}` (builder) -> "
        f"`agent/task-{task.id}-tester` (tester) -> "
        f"`agent/task-{task.id}-reviewer` (reviewer).",
    ]

    if task.github_issue_number:
        lines.extend(["", f"Closes #{task.github_issue_number}"])

    lines.extend(["", "Review the diff before merging; nothing has been merged."])
    return "\n".join(lines)


class GhPullRequestPublisher:
    """Open pull requests through the `gh` CLI. Never merges."""

    def __init__(
        self,
        repository_path: Path | None = None,
        github_repo: str | None = None,
        base_branch: str = "main",
    ) -> None:
        self.repository_path = repository_path
        self.github_repo = github_repo
        self.base_branch = base_branch

    def open_pull_request(self, task: Task, head: str) -> str | None:
        command = [
            "gh",
            "pr",
            "create",
            "--base",
            self.base_branch,
            "--head",
            head,
            "--title",
            f"{task.title} (agent task {task.id})",
            "--body",
            pull_request_body(task),
        ]

        if self.github_repo:
            command.extend(["--repo", self.github_repo])

        try:
            result = subprocess.run(
                command,
                cwd=self.repository_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", None) or type(exc).__name__
            logger.warning(
                "Could not open pull request: task_id=%s error=%s",
                task.id,
                str(detail).strip(),
            )
            return None

        lines = result.stdout.strip().splitlines()
        return lines[-1].strip() if lines else None
