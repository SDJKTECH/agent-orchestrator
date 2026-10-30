# Finds tasks whose pull request has been merged, so their cards/issues can finish.
import logging
import re
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.db.models import Task
from orchestrator.app.github.client import GitHubApi
from orchestrator.app.github.projects import DONE, PR_OPEN

logger = logging.getLogger(__name__)

_PULL_NUMBER = re.compile(r"/pull/(\d+)")


def sync_merged_tasks(
    session: Session,
    pulls: GitHubApi | None,
    move: Callable[[Task, str], bool],
) -> list[int]:
    """Move GitHub tasks (the ones not from Jira) to Done once their PR is merged."""
    if pulls is None:
        return []

    done: list[int] = []
    waiting = session.scalars(
        select(Task).where(
            Task.board_column == PR_OPEN,
            Task.pull_request_url.is_not(None),
            Task.jira_key.is_(None),
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
