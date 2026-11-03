# Mirrors a task's progress on its Jira issue: moves the status, adds comments.
# Failures are logged and swallowed, so a Jira outage never fails an agent run.
import logging
import time

from sqlalchemy.orm import Session

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.db.models import Task
from orchestrator.app.github.client import GitHubApi, detect_github_repo
from orchestrator.app.jira.client import JiraApi, make_jira_api
from orchestrator.app.jira.columns import (
    BLOCKED,
    BUILDING,
    CHECKS,
    DONE,
    FAILED,
    FIXING,
    PR_OPEN,
    QUEUED,
    REVIEWING,
    TESTING,
)
from orchestrator.app.jira.merged import (
    PullRequests,
    sync_merged_stories,
    sync_merged_tasks,
)
from orchestrator.app.jira.messages import outcome_message
from orchestrator.app.jira.models import JiraItem
from orchestrator.app.jira.progress import sync_progress
from orchestrator.app.planning.dependencies import describe_blockers

logger = logging.getLogger(__name__)

# Jira answers 404 for an issue that was deleted and also for one it will not show
# to a caller whose login failed. An issue reported missing is asked about again
# after this long, so a fixed login is noticed without a restart.
MISSING_RETRY_SECONDS = 600

# Jira status to move an issue to for each pipeline column. Jira workflows differ,
# so JIRA_STATUS_MAP can change these; "-" leaves the issue where it is.
DEFAULT_STATUS_MAP = {
    QUEUED: "In Progress",
    BUILDING: "In Progress",
    TESTING: "In Progress",
    CHECKS: "In Progress",
    REVIEWING: "In Progress",
    FIXING: "In Progress",
    PR_OPEN: "In Review",
    DONE: "Done",
    FAILED: "To Do",
}


def parse_status_map(text: str | None) -> dict[str, str]:
    """The default map, changed by "Column=Status,Column=Status" overrides."""
    status_map = dict(DEFAULT_STATUS_MAP)

    for pair in (text or "").split(","):
        column, _, status = pair.partition("=")
        column, status = column.strip(), status.strip()

        if not column or not status:
            continue

        if status == "-":
            status_map.pop(column, None)
        else:
            status_map[column] = status

    return status_map


class JiraTracker:
    """Keeps a task's Jira issue in step with its pipeline column."""

    def __init__(
        self,
        api: JiraApi,
        status_map: dict[str, str] | None = None,
        pulls: PullRequests | None = None,
    ) -> None:
        self.api = api
        self.status_map = DEFAULT_STATUS_MAP if status_map is None else status_map
        self.pulls = pulls
        # Issues Jira says do not exist (deleted, or not visible) -> when to retry.
        self._gone: dict[str, float] = {}

    def move(self, task: Task, column: str) -> bool:
        """Show `column` on the task's Jira issue. False when Jira could not be told."""
        key = task.jira_key

        if not key or task.board_column == column:
            return False

        try:
            target = self.status_map.get(column)
            if target:
                self._transition(key, target)

            message = self._message(task, column)
            if message:
                self.api.comment(key, message)
        except OrchestratorError as exc:
            logger.warning(
                "Could not update Jira: task_id=%s issue=%s column=%s error=%s",
                task.id,
                key,
                column,
                exc,
            )
            return False

        task.board_column = column
        session = Session.object_session(task)
        if session is not None:
            session.commit()

        logger.info("Jira updated: task_id=%s issue=%s column=%s", task.id, key, column)
        return True

    def _transition(self, key: str, target: str) -> None:
        """Move the issue to the status named `target`, if Jira offers a way there."""
        wanted = target.casefold()

        if (self.api.status_name(key) or "").casefold() == wanted:
            return

        for item in self.api.transitions(key):
            destination = (item.get("to") or {}).get("name", "")
            name = item.get("name", "")

            if wanted in (destination.casefold(), name.casefold()):
                self.api.transition(key, str(item["id"]))
                return

        # The workflow has no such step from the current status; carry on.
        logger.warning(
            "Jira issue %s cannot move to '%s' from its status.", key, target
        )

    def link_blocks(self, blocked: Task, blocker: Task) -> bool:
        """Add Jira's "is blocked by" link, so a person can see (and delete) it."""
        if not blocked.jira_key or not blocker.jira_key:
            return False

        try:
            self.api.link_blocks(blocker.jira_key, blocked.jira_key)
        except OrchestratorError as exc:
            logger.warning(
                "Could not link %s to %s: %s", blocked.jira_key, blocker.jira_key, exc
            )
            return False

        return True

    @staticmethod
    def _message(task: Task, column: str) -> str | None:
        """The comment to post for a column change, or None for no comment."""
        if column == BLOCKED:
            session = Session.object_session(task)
            waiting = describe_blockers(session, task) if session else "other tasks"
            return (
                f"Waiting for {waiting} to be merged. The orchestrator starts this "
                "issue afterwards, from the merged code."
            )

        if column == QUEUED:
            return (
                f"Agent orchestrator picked up this issue as task {task.id} "
                f"(branch `{task.branch_name}`). Running builder, tester and reviewer."
            )

        if column == FIXING:
            return f"Task {task.id}: a stage failed; the builder is fixing it."

        if column == DONE and task.board_column == PR_OPEN:
            return f"The pull request was merged: {task.pull_request_url}"

        if column in (PR_OPEN, DONE, FAILED):
            return outcome_message(task)

        return None

    def sync_merged(self, session: Session) -> list[int]:
        """Mark tasks whose pull request has merged as Done in Jira."""
        sync_merged_stories(session, self.pulls, self._finish_story)
        sync_progress(
            session, self.api, self.status_map, self.set_status, self.current_status
        )
        return sync_merged_tasks(session, self.pulls, self.move)

    def current_status(self, key: str) -> str | None:
        """The issue's status in Jira now; None if it cannot be read."""
        if self._is_missing(key):
            return None

        try:
            return self.api.status_name(key)
        except OrchestratorError as exc:
            self._note_missing(key, exc)
            return None

    def set_status(self, key: str, target: str) -> bool:
        """Move an issue to the status named `target`. False if Jira cannot be told.

        An issue Jira reports as not found is left alone for a while, so a deleted
        Story is not asked about on every poll.
        """
        if self._is_missing(key):
            return False

        try:
            self._transition(key, target)
        except OrchestratorError as exc:
            if not self._note_missing(key, exc):
                logger.warning("Could not move %s to '%s': %s", key, target, exc)
            return False

        self._gone.pop(key, None)
        return True

    def _is_missing(self, key: str) -> bool:
        """True while an issue Jira reported as not found is being left alone."""
        return self._gone.get(key, 0.0) > time.monotonic()

    def _note_missing(self, key: str, exc: OrchestratorError) -> bool:
        """Remember a 'not found' answer; False if the error was something else."""
        if "failed with 404" not in str(exc):
            return False

        self._gone[key] = time.monotonic() + MISSING_RETRY_SECONDS
        logger.warning(
            "%s was not found in Jira (deleted, or not visible to this login); "
            "asking again in %s minutes.",
            key,
            MISSING_RETRY_SECONDS // 60,
        )
        return True

    def _finish_story(self, item: JiraItem) -> bool:
        """Move a Story to Done in Jira once its pull request is merged."""
        try:
            target = self.status_map.get(DONE)
            if target:
                self._transition(item.key, target)
                item.jira_status = target
            self.api.comment(
                item.key, f"The pull request was merged: {item.pull_request_url}"
            )
        except OrchestratorError as exc:
            logger.warning("Could not finish Story %s in Jira: %s", item.key, exc)
            return False

        return True


def make_jira_tracker(settings: Settings) -> JiraTracker:
    """Build the Jira tracker from the settings.

    Pull requests live on GitHub, so this is where the GitHub client is made.
    """
    repo = settings.github_repo or detect_github_repo(settings.repository_path)
    token = settings.github_token.get_secret_value() if settings.github_token else None
    return JiraTracker(
        make_jira_api(settings),
        parse_status_map(settings.jira_status_map),
        GitHubApi(repo, token) if repo else None,
    )
