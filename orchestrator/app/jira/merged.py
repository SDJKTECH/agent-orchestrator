# Finds Jira tasks whose pull request has been merged, so their issue can finish.
import logging
import re
from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.db.models import Task
from orchestrator.app.jira.columns import DONE, PR_OPEN
from orchestrator.app.jira.models import JiraItem

logger = logging.getLogger(__name__)

_PULL_NUMBER = re.compile(r"/pull/(\d+)")


class PullRequests(Protocol):
    """Reads pull requests of the repository the tasks' branches are pushed to."""

    def request(self, method: str, path: str, body: dict | None = None) -> Any: ...


def sync_merged_tasks(
    session: Session,
    pulls: PullRequests | None,
    move: Callable[[Task, str], bool],
) -> list[int]:
    """Move Jira tasks to Done once their pull request is merged."""
    if pulls is None:
        return []

    done: list[int] = []
    waiting = session.scalars(
        select(Task).where(
            Task.board_column == PR_OPEN,
            Task.pull_request_url.is_not(None),
            Task.jira_key.is_not(None),
        )
    )

    for task in waiting:
        match = _PULL_NUMBER.search(task.pull_request_url or "")

        if match is None:
            continue

        try:
            pull = pulls.request("GET", f"/pulls/{match[1]}")
        except OrchestratorError as exc:
            logger.warning("Could not read pull request: %s", exc)
            continue

        if isinstance(pull, dict) and pull.get("merged") and move(task, DONE):
            done.append(task.id)

    return done


def sync_merged_stories(
    session: Session,
    pulls: PullRequests | None,
    finish: Callable[[JiraItem], bool],
) -> list[str]:
    """Finish each Story whose pull request was merged; return their keys."""
    if pulls is None:
        return []

    done: list[str] = []
    waiting = session.scalars(
        select(JiraItem).where(
            JiraItem.pull_request_url.is_not(None),
            JiraItem.pull_request_merged.is_(False),
        )
    )

    for item in waiting:
        match = _PULL_NUMBER.search(item.pull_request_url or "")

        if match is None:
            continue

        try:
            pull = pulls.request("GET", f"/pulls/{match[1]}")
        except OrchestratorError as exc:
            logger.warning("Could not read pull request: %s", exc)
            continue

        if isinstance(pull, dict) and pull.get("merged"):
            item.pull_request_merged = True
            session.commit()
            finish(item)
            done.append(item.key)

    return done
