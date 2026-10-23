import logging
import subprocess
from pathlib import Path
from typing import Protocol

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.db.models import Task
from orchestrator.app.github.client import GitHubApi, detect_github_repo

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


class RestPullRequestPublisher:
    """Open pull requests through the REST API.

    Without a token it cannot write, so it returns the GitHub compare link
    where the pull request can be opened by hand.
    """

    def __init__(self, api: GitHubApi, base_branch: str = "main") -> None:
        self.api = api
        self.base_branch = base_branch

    def open_pull_request(self, task: Task, head: str) -> str | None:
        if not self.api.can_write:
            return (
                f"https://github.com/{self.api.repo}/compare/"
                f"{self.base_branch}...{head}?expand=1"
            )

        try:
            created = self.api.request(
                "POST",
                "/pulls",
                {
                    "title": f"{task.title} (agent task {task.id})",
                    "head": head,
                    "base": self.base_branch,
                    "body": pull_request_body(task),
                },
            )
        except OrchestratorError as exc:
            logger.warning(
                "Could not open pull request: task_id=%s error=%s", task.id, exc
            )
            return None

        return created.get("html_url") if isinstance(created, dict) else None


def make_publisher(settings: Settings) -> PullRequestPublisher:
    """Create the pull request publisher for the configured GitHub backend."""
    if settings.github_backend == "rest":
        repo = settings.github_repo or detect_github_repo(settings.repository_path)
        token = (
            settings.github_token.get_secret_value() if settings.github_token else None
        )

        if repo is not None:
            return RestPullRequestPublisher(
                GitHubApi(repo, token), settings.base_branch
            )

    return GhPullRequestPublisher(
        repository_path=settings.repository_path,
        github_repo=settings.github_repo,
        base_branch=settings.base_branch,
    )
