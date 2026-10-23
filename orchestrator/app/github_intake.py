import json
import logging
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.config import Settings
from orchestrator.app.exceptions import IssueSourceError, OrchestratorError
from orchestrator.app.github_api import GitHubApi, detect_github_repo
from orchestrator.app.models import Task
from orchestrator.app.task_service import create_task

logger = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 250
MAX_DESCRIPTION_LENGTH = 10000


@dataclass(frozen=True)
class Issue:
    """A work item read from GitHub (or a stand-in file)."""

    number: int
    title: str
    body: str = ""
    labels: list[str] = field(default_factory=list)


class IssueSource(Protocol):
    """Where open issues come from; replaceable in tests and offline demos."""

    def list_issues(self) -> list[Issue]: ...


def _parse_issues(items: object) -> list[Issue]:
    if not isinstance(items, list):
        raise IssueSourceError("Issue data must be a JSON list.")

    issues: list[Issue] = []

    for item in items:
        if not isinstance(item, dict):
            raise IssueSourceError("Each issue must be a JSON object.")

        number = item.get("number")
        title = item.get("title")

        if isinstance(number, bool) or not isinstance(number, int):
            raise IssueSourceError("Each issue needs an integer 'number'.")

        if not isinstance(title, str) or not title.strip():
            raise IssueSourceError(f"Issue {number} needs a non-empty 'title'.")

        labels = [
            label["name"] if isinstance(label, dict) else str(label)
            for label in item.get("labels") or []
            if not isinstance(label, dict) or "name" in label
        ]
        issues.append(
            Issue(
                number=number,
                title=title.strip(),
                body=item.get("body") or "",
                labels=labels,
            )
        )

    return issues


class FileIssueSource:
    """Read issues from a JSON file shaped like `gh issue list --json` output."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def list_issues(self) -> list[Issue]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise IssueSourceError(f"Could not read issue file: {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise IssueSourceError(
                f"Issue file is not valid JSON: {self.path}"
            ) from exc

        return _parse_issues(data)


class GhCliIssueSource:
    """Read open issues through the authenticated GitHub CLI."""

    def __init__(
        self,
        repository_path: Path | None = None,
        github_repo: str | None = None,
        limit: int = 50,
        label: str | None = None,
    ) -> None:
        self.repository_path = repository_path
        self.github_repo = github_repo
        self.limit = limit
        self.label = label

    def list_issues(self) -> list[Issue]:
        command = [
            "gh",
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            str(self.limit),
            "--json",
            "number,title,body,labels",
        ]

        if self.github_repo:
            command.extend(["--repo", self.github_repo])

        if self.label:
            command.extend(["--label", self.label])

        try:
            result = subprocess.run(
                command,
                cwd=self.repository_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise IssueSourceError(
                "GitHub CLI (gh) was not found. Install it and run `gh auth login`, "
                "or set ISSUE_SOURCE=file."
            ) from exc
        except subprocess.CalledProcessError as exc:
            message = (exc.stderr or "").strip() or "Unknown gh error"
            raise IssueSourceError(f"gh issue list failed: {message}") from exc
        except subprocess.TimeoutExpired as exc:
            raise IssueSourceError("gh issue list timed out.") from exc

        try:
            return _parse_issues(json.loads(result.stdout))
        except json.JSONDecodeError as exc:
            raise IssueSourceError("gh returned invalid JSON.") from exc


class RestIssueSource:
    """Read open issues through the GitHub REST API (public repos need no token)."""

    def __init__(self, api: GitHubApi, label: str | None = None, limit: int = 50):
        self.api = api
        self.label = label
        self.limit = limit

    def list_issues(self) -> list[Issue]:
        path = f"/issues?state=open&per_page={self.limit}"

        if self.label:
            path += f"&labels={urllib.parse.quote(self.label)}"

        items = self.api.request("GET", path)

        if not isinstance(items, list):
            raise IssueSourceError("GitHub returned an unexpected issues payload.")

        # The issues endpoint also returns pull requests; skip them.
        return _parse_issues([item for item in items if "pull_request" not in item])


def build_issue_source(settings: Settings) -> IssueSource:
    """Create the issue source selected in settings."""
    if settings.issue_source == "file":
        if settings.issue_file_path is None:
            raise IssueSourceError(
                "ISSUE_FILE_PATH is required when ISSUE_SOURCE=file."
            )
        return FileIssueSource(settings.issue_file_path)

    if settings.github_backend == "rest":
        repo = settings.github_repo or detect_github_repo(settings.repository_path)

        if repo is None:
            raise IssueSourceError(
                "Set GITHUB_REPO (owner/name); it could not be detected from origin."
            )

        token = (
            settings.github_token.get_secret_value() if settings.github_token else None
        )
        return RestIssueSource(GitHubApi(repo, token), label=settings.issue_label)

    return GhCliIssueSource(
        repository_path=settings.repository_path,
        github_repo=settings.github_repo,
        label=settings.issue_label,
    )


@dataclass
class SyncResult:
    imported: list[Task] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)


def sync_issues(
    session: Session,
    milestone_id: int,
    source: IssueSource,
    worktrees_root: Path,
) -> SyncResult:
    """Create a queued task (with worktree) for each issue not yet imported."""
    issues = source.list_issues()

    existing = set(
        session.scalars(
            select(Task.github_issue_number).where(
                Task.milestone_id == milestone_id,
                Task.github_issue_number.is_not(None),
            )
        )
    )

    result = SyncResult()

    for issue in issues:
        if issue.number in existing:
            result.skipped.append(issue.number)
            continue

        try:
            task = create_task(
                session=session,
                milestone_id=milestone_id,
                title=issue.title[:MAX_TITLE_LENGTH],
                description=issue.body[:MAX_DESCRIPTION_LENGTH],
                worktrees_root=worktrees_root,
                github_issue_number=issue.number,
            )
        except (OrchestratorError, ValueError) as exc:
            logger.warning("Issue import failed: issue=%s error=%s", issue.number, exc)
            result.failed[issue.number] = str(exc)
            continue

        existing.add(issue.number)
        result.imported.append(task)

    return result
