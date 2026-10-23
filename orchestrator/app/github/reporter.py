# Posts progress and result comments on a task's GitHub issue.
# Includes the pull request link and the cost summary.
import logging
import subprocess
from pathlib import Path

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.db.models import Task
from orchestrator.app.github.client import GitHubApi, detect_github_repo

logger = logging.getLogger(__name__)


class GhIssueReporter:
    """Post progress comments on GitHub issues through the `gh` CLI.

    Failures are logged and swallowed: reporting must never break a run.
    """

    def __init__(
        self,
        repository_path: Path | None = None,
        github_repo: str | None = None,
    ) -> None:
        self.repository_path = repository_path
        self.github_repo = github_repo

    def comment(self, issue_number: int, body: str) -> bool:
        command = ["gh", "issue", "comment", str(issue_number), "--body", body]

        if self.github_repo:
            command.extend(["--repo", self.github_repo])

        try:
            subprocess.run(
                command,
                cwd=self.repository_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(
                "Could not comment on issue %s: %s", issue_number, type(exc).__name__
            )
            return False

        return True


class RestIssueReporter:
    """Post issue comments through the REST API. Needs a token to write."""

    def __init__(self, api: GitHubApi) -> None:
        self.api = api

    def comment(self, issue_number: int, body: str) -> bool:
        if not self.api.can_write:
            logger.info("No GitHub token configured; skipping issue comment.")
            return False

        try:
            self.api.request("POST", f"/issues/{issue_number}/comments", {"body": body})
        except OrchestratorError as exc:
            logger.warning("Could not comment on issue %s: %s", issue_number, exc)
            return False

        return True


def make_reporter(settings: Settings) -> "GhIssueReporter | RestIssueReporter":
    """Create the issue reporter for the configured GitHub backend."""
    if settings.github_backend == "rest":
        repo = settings.github_repo or detect_github_repo(settings.repository_path)
        token = (
            settings.github_token.get_secret_value() if settings.github_token else None
        )

        if repo is not None:
            return RestIssueReporter(GitHubApi(repo, token))

    return GhIssueReporter(settings.repository_path, settings.github_repo)


def picked_up_message(task: Task) -> str:
    return (
        f"Agent orchestrator picked up this issue as task {task.id} "
        f"(branch `{task.branch_name}`). Running builder, tester and reviewer."
    )


def outcome_message(task: Task) -> str:
    """Summarize a finished task: status, per-role runs, cost and checks."""
    lines = [f"Task {task.id} finished with status **{task.status.value}**.", ""]

    total_cost = 0.0
    for run in task.agent_runs:
        cost = run.reported_cost_usd or 0.0
        total_cost += cost
        lines.append(
            f"- {run.agent_role} ({run.model or '?'}/{run.effort or '?'}): "
            f"{run.status}, {run.input_tokens} in / {run.output_tokens} out "
            f"tokens, ${cost:.4f}"
        )

    if task.verification_results:
        lines.append("")
        lines.extend(
            f"- check `{result.check_name}`: {'passed' if result.passed else 'failed'}"
            for result in task.verification_results
        )

    if task.pull_request_url:
        lines.extend(["", f"Pull request: {task.pull_request_url}"])

    lines.extend(["", f"Total reported cost: ${total_cost:.4f}"])
    return "\n".join(lines)
