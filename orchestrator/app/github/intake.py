# Reads open GitHub issues (API, gh CLI or a JSON file) and turns them into tasks.
# Each issue is imported once and filed under its GitHub milestone.
import json
import logging
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import IssueSourceError, OrchestratorError
from orchestrator.app.db.models import Milestone, Project, Task
from orchestrator.app.github.client import GitHubApi, detect_github_repo
from orchestrator.app.github.projects import (
    TODO,
    BoardCard,
    ProjectsApi,
    make_projects_apis,
)
from orchestrator.app.services.task_service import create_task

logger = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 250
MAX_DESCRIPTION_LENGTH = 10000
MAX_MILESTONE_TITLE_LENGTH = 200
MAX_MILESTONE_CODE_LENGTH = 20
MAX_PROJECT_NAME_LENGTH = 150

# Milestone for issues that have no GitHub milestone.
MILESTONE_CODE = "GH"
MILESTONE_TITLE = "GitHub issues"


@dataclass(frozen=True)
class Issue:
    """A work item read from GitHub (or a stand-in file)."""

    number: int
    title: str
    body: str = ""
    labels: list[str] = field(default_factory=list)
    milestone: str | None = None
    milestone_number: int | None = None
    project_item_id: str | None = None
    # The Projects board the issue came from; each board is its own project.
    board_number: int | None = None
    board_title: str | None = None
    # Set for Jira issues, which have no GitHub issue number (`number` is 0).
    jira_key: str | None = None
    # Jira keys this issue is blocked by (its "is blocked by" links).
    blocked_by: list[str] = field(default_factory=list)


class IssueSource(Protocol):
    """Where open issues come from; replaceable in tests and offline demos."""

    def list_issues(self) -> list[Issue]: ...


def _parse_issues(items: object) -> list[Issue]:
    """Turn a list of raw issue dicts into Issue objects, skipping malformed ones."""
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
        milestone = item.get("milestone")
        milestone_title = milestone_number = None
        if isinstance(milestone, dict):
            milestone_title = milestone.get("title")
            milestone_number = milestone.get("number")
        elif isinstance(milestone, str):
            milestone_title = milestone

        issues.append(
            Issue(
                number=number,
                title=title.strip(),
                body=item.get("body") or "",
                labels=labels,
                milestone=(
                    milestone_title.strip()
                    if isinstance(milestone_title, str) and milestone_title.strip()
                    else None
                ),
                milestone_number=(
                    milestone_number if isinstance(milestone_number, int) else None
                ),
            )
        )

    return issues


class FileIssueSource:
    """Read issues from a JSON file shaped like `gh issue list --json` output."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def list_issues(self) -> list[Issue]:
        """Read the issues from a JSON file."""
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
        """List open labelled issues with the `gh` CLI."""
        command = [
            "gh",
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            str(self.limit),
            "--json",
            "number,title,body,labels,milestone",
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
        """List open labelled issues with the GitHub REST API."""
        path = f"/issues?state=open&per_page={self.limit}"

        if self.label:
            path += f"&labels={urllib.parse.quote(self.label)}"

        items = self.api.request("GET", path)

        if not isinstance(items, list):
            raise IssueSourceError("GitHub returned an unexpected issues payload.")

        # The issues endpoint also returns pull requests; skip them.
        return _parse_issues([item for item in items if "pull_request" not in item])


# Draft cards that could not be turned into issues (already reported).
_UNCONVERTED_DRAFTS: set[str] = set()


class ProjectIssueSource:
    """Read the issues sitting in the Todo column of a GitHub Projects board."""

    def __init__(self, api: ProjectsApi, repo: str | None = None) -> None:
        self.api = api
        self.repo = repo

    def _issue_card(self, card: BoardCard) -> BoardCard | None:
        """Drafts in Todo become real issues; they are the only cards changed."""
        if not card.draft:
            return card

        if card.column != TODO or self.repo is None:
            return None

        try:
            return self.api.convert_draft(card.item_id, self.repo)
        except OrchestratorError as exc:
            # Report each draft once, not on every poll.
            if card.item_id not in _UNCONVERTED_DRAFTS:
                _UNCONVERTED_DRAFTS.add(card.item_id)
                logger.warning(
                    "Draft task not converted to an issue (the token may need "
                    "the public_repo scope): board=%s error=%s",
                    self.api.number,
                    exc,
                )
            return None

    def list_issues(self) -> list[Issue]:
        """List the issues whose cards sit in the board's Todo column."""
        cards = self.api.load()

        # A new board only has the default columns; add ours so cards can move.
        if self.api.missing_columns():
            try:
                self.api.ensure_columns()
            except OrchestratorError as exc:
                logger.warning(
                    "Board skipped, its columns could not be set up: board=%s error=%s",
                    self.api.number,
                    exc,
                )
                return []

        if self.api.missing_columns():
            return []

        cards = [self._issue_card(card) for card in cards]
        cards = [card for card in cards if card is not None]

        return [
            Issue(
                number=card.number,
                title=card.title.strip() or f"Issue #{card.number}",
                body=card.body,
                labels=card.labels,
                milestone=card.milestone,
                milestone_number=card.milestone_number,
                project_item_id=card.item_id,
                board_number=self.api.number,
                board_title=self.api.title,
            )
            for card in cards
            if card.column == TODO
            and card.state == "OPEN"
            and (self.repo is None or card.repo.lower() == self.repo.lower())
        ]


class CombinedIssueSource:
    """Several issue sources in one; an issue on two boards is taken once."""

    def __init__(self, sources: list[IssueSource]) -> None:
        self.sources = sources

    def list_issues(self) -> list[Issue]:
        """List the issues of every board, without duplicates."""
        issues: dict[int, Issue] = {}

        for source in self.sources:
            for issue in source.list_issues():
                issues.setdefault(issue.number, issue)

        return list(issues.values())


def build_issue_source(settings: Settings) -> IssueSource:
    """Create the issue source selected in settings."""
    if settings.issue_source == "jira":
        # Imported here: the Jira source builds on `Issue` from this module.
        from orchestrator.app.jira.source import make_jira_source

        return make_jira_source(settings)

    if settings.issue_source == "project":
        if (settings.project_token or settings.github_token) is None:
            raise IssueSourceError(
                "ISSUE_SOURCE=project needs a PROJECT_TOKEN (or GITHUB_TOKEN) with "
                "the 'project' scope."
            )

        apis = make_projects_apis(settings)

        if not apis:
            logger.info(
                "No Projects board is linked to the repository yet; link one in "
                "its Projects tab and add an issue to its Todo column."
            )

        repo = settings.github_repo or detect_github_repo(settings.repository_path)
        return CombinedIssueSource([ProjectIssueSource(api, repo) for api in apis])

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
    """What one import did: tasks created, issues skipped, failures and Jira links."""

    imported: list[Task] = field(default_factory=list)
    skipped: list[int | str] = field(default_factory=list)
    failed: dict[int | str, str] = field(default_factory=dict)
    # Task id -> Jira keys of the issues that block it.
    links: dict[int, list[str]] = field(default_factory=dict)


def milestone_for_issue(
    session: Session, project_id: int, issue: Issue, default_id: int
) -> int:
    """Return the internal milestone matching the issue's GitHub milestone.

    The milestone is created on first use. Issues without one use `default_id`.
    """
    if not issue.milestone:
        return default_id

    title = issue.milestone[:MAX_MILESTONE_TITLE_LENGTH]
    milestone = session.scalar(
        select(Milestone).where(
            Milestone.project_id == project_id, Milestone.title == title
        )
    )

    if milestone is None:
        # "M2 - Git and Worktree Management" -> code "M2".
        milestone = Milestone(
            project_id=project_id,
            code=title.split()[0][:MAX_MILESTONE_CODE_LENGTH],
            title=title,
            github_milestone_number=issue.milestone_number,
        )
        session.add(milestone)
        session.flush()

    return milestone.id


def default_milestone(session: Session, project_id: int) -> int:
    """The milestone for a project's issues that have no GitHub milestone."""
    milestone = session.scalar(
        select(Milestone).where(
            Milestone.project_id == project_id, Milestone.code == MILESTONE_CODE
        )
    )

    if milestone is None:
        milestone = Milestone(
            project_id=project_id, code=MILESTONE_CODE, title=MILESTONE_TITLE
        )
        session.add(milestone)
        session.flush()

    return milestone.id


def board_project(session: Session, template: Project, board_title: str) -> Project:
    """The project for a Projects board, created on first use.

    Every board works in the same repository clone as `template`.
    """
    name = board_title.strip()[:MAX_PROJECT_NAME_LENGTH]
    project = session.scalar(select(Project).where(Project.name == name))

    if project is None:
        project = Project(
            name=name,
            local_path=template.local_path,
            repository_url=template.repository_url,
        )
        session.add(project)
        session.flush()

    return project


def sync_issues(
    session: Session,
    milestone_id: int,
    source: IssueSource,
    worktrees_root: Path,
) -> SyncResult:
    """Create a queued task (with worktree) for each issue not yet imported.

    Issues land in the internal milestone that mirrors their GitHub milestone;
    issues without one go to `milestone_id`. Issues that come from a Projects board
    go to a project named after that board. An issue is imported once per repository.
    """
    issues = source.list_issues()
    default = session.get(Milestone, milestone_id)
    project_id = default.project_id if default else None

    def known(column):
        """Query for the values of `column` already stored for this project."""
        query = (
            select(column)
            .join(Milestone, Task.milestone_id == Milestone.id)
            .where(column.is_not(None))
        )
        if default is not None and default.project.local_path:
            return query.join(Project, Milestone.project_id == Project.id).where(
                Project.local_path == default.project.local_path
            )
        return query.where(Milestone.project_id == project_id)

    existing = set(session.scalars(known(Task.github_issue_number)))
    existing_jira = set(session.scalars(known(Task.jira_key)))

    result = SyncResult()

    for issue in issues:
        if issue.jira_key:
            if issue.jira_key in existing_jira:
                result.skipped.append(issue.jira_key)
                continue
        elif issue.number in existing:
            result.skipped.append(issue.number)
            continue

        try:
            target_project, target_default = project_id, milestone_id
            if default is not None and issue.board_title:
                board = board_project(session, default.project, issue.board_title)
                target_project = board.id
                target_default = default_milestone(session, board.id)

            task = create_task(
                session=session,
                milestone_id=(
                    milestone_for_issue(session, target_project, issue, target_default)
                    if target_project is not None
                    else milestone_id
                ),
                title=issue.title[:MAX_TITLE_LENGTH],
                description=issue.body[:MAX_DESCRIPTION_LENGTH],
                worktrees_root=worktrees_root,
                github_issue_number=None if issue.jira_key else issue.number,
                project_item_id=issue.project_item_id,
                board_number=issue.board_number,
                jira_key=issue.jira_key,
            )
        except (OrchestratorError, ValueError) as exc:
            logger.warning(
                "Issue import failed: issue=%s error=%s",
                issue.jira_key or issue.number,
                exc,
            )
            result.failed[issue.jira_key or issue.number] = str(exc)
            continue

        if issue.jira_key:
            existing_jira.add(issue.jira_key)
            if issue.blocked_by:
                result.links[task.id] = list(issue.blocked_by)
        else:
            existing.add(issue.number)
        result.imported.append(task)

    return result
