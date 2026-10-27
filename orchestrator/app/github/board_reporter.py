# Moves a task's card across the GitHub Projects board as its stages progress.
# Failures are logged and swallowed, so a GitHub outage never fails an agent run.
import logging
import time
from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.db.models import Task
from orchestrator.app.github.client import GitHubApi, detect_github_repo
from orchestrator.app.github.merged import sync_merged_tasks
from orchestrator.app.github.projects import (
    ProjectsApi,
    board_numbers,
    make_projects_apis,
)
from orchestrator.app.jira.tracker import make_jira_tracker

logger = logging.getLogger(__name__)


class BoardMover:
    """Move task cards between board columns and detect merged pull requests."""

    def __init__(
        self,
        projects: ProjectsApi | list[ProjectsApi],
        pulls: GitHubApi | None = None,
        refresh: Callable[[], list[ProjectsApi]] | None = None,
    ) -> None:
        boards = projects if isinstance(projects, list) else [projects]
        self.boards = {board.number: board for board in boards}
        self.pulls = pulls
        # Finds boards linked after start-up; rate limited when it is used.
        self.refresh = refresh
        self._refreshed_at = float("-inf")

    def _reload_boards(self) -> None:
        if self.refresh is None or time.monotonic() - self._refreshed_at < 30:
            return

        self._refreshed_at = time.monotonic()

        try:
            for board in self.refresh():
                self.boards.setdefault(board.number, board)
        except OrchestratorError as exc:
            logger.warning("Could not look up the project boards: %s", exc)

    def _board_for(self, task: Task) -> ProjectsApi | None:
        if task.board_number not in self.boards:
            self._reload_boards()

        if task.board_number in self.boards:
            return self.boards[task.board_number]

        # Tasks imported before several boards were supported have no number.
        return next(iter(self.boards.values())) if len(self.boards) == 1 else None

    def move(self, task: Task, column: str) -> bool:
        """Put the task's card in `column`. False when it could not be moved."""
        board = self._board_for(task)

        if board is None or not task.project_item_id or task.board_column == column:
            return False

        try:
            board.set_column(task.project_item_id, column)
        except OrchestratorError as exc:
            logger.warning(
                "Could not move card: task_id=%s column=%s error=%s",
                task.id,
                column,
                exc,
            )
            return False

        task.board_column = column
        session = Session.object_session(task)
        if session is not None:
            session.commit()

        logger.info("Card moved: task_id=%s column=%s", task.id, column)
        return True

    def sync_merged(self, session: Session) -> list[int]:
        """Move cards to Done once their pull request has been merged."""
        return sync_merged_tasks(session, self.pulls, self.move, jira=False)

    def link_blocks(self, blocked: Task, blocker: Task) -> bool:
        """GitHub boards have no dependency links; nothing to record."""
        return False


class TaskBoard(Protocol):
    """What the pipeline and poller need from a board or tracker."""

    def move(self, task: Task, column: str) -> bool: ...

    def sync_merged(self, session: Session) -> list[int]: ...

    def link_blocks(self, blocked: Task, blocker: Task) -> bool: ...


def make_board(settings: Settings) -> TaskBoard | None:
    """Create the card mover (or Jira tracker), or None when none is configured."""
    if settings.issue_source == "jira":
        return make_jira_tracker(settings)

    try:
        explicit = bool(board_numbers(settings))
    except OrchestratorError:
        return None

    if settings.issue_source != "project" and not explicit:
        return None

    try:
        projects = make_projects_apis(settings)
    except OrchestratorError as exc:
        # The boards are looked up again when a card needs to be moved.
        logger.warning("Could not look up the project boards: %s", exc)
        projects = []

    if not projects and (settings.project_token or settings.github_token) is None:
        return None

    repo = settings.github_repo or detect_github_repo(settings.repository_path)
    token = settings.github_token.get_secret_value() if settings.github_token else None
    pulls = GitHubApi(repo, token) if repo else None
    return BoardMover(projects, pulls, refresh=lambda: make_projects_apis(settings))
