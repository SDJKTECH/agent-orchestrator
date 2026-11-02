# Keeps the Jira status of Stories and Epics in step with the work under them.
# Worked out afresh from the database on every poll, so a missed update is repaired
# on the next one. A Story is In Progress once a Subtask has started, In Review when
# its pull request is open and Done when it is merged. An Epic is In Progress once a
# Story has started and Done when no Story under it is left unfinished in Jira.
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.db.models import Task, TaskStatus
from orchestrator.app.jira.client import JiraApi
from orchestrator.app.jira.columns import BUILDING, DONE, PR_OPEN
from orchestrator.app.jira.models import JiraItem
from orchestrator.app.jira.stories import STORY_TYPE, story_tasks

logger = logging.getLogger(__name__)

EPIC_TYPE = "epic"

# A task in one of these statuses has been picked up by the agents.
_STARTED = (
    TaskStatus.STARTING,
    TaskStatus.RUNNING,
    TaskStatus.VERIFYING,
    TaskStatus.PAUSED,
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
)

# Moves an issue to a Jira status by name; False if Jira could not be told.
SetStatus = Callable[[str, str], bool]


@dataclass(frozen=True)
class Move:
    """One Story or Epic that was moved, and the Jira status it was moved to."""

    key: str
    status: str


def story_column(session: Session, item: JiraItem) -> str | None:
    """The pipeline column a Story is in: Building, PR open, Done, or None.

    None means no Subtask of the Story has started, so Jira is left alone.
    """
    if item.pull_request_merged:
        return DONE

    if item.pull_request_url:
        return PR_OPEN

    tasks = [t for t in story_tasks(session, item) if t.status != TaskStatus.CANCELLED]

    if any(_started(task) for task in tasks):
        return BUILDING

    return None


def _started(task: Task) -> bool:
    return task.status in _STARTED or task.story_branch is not None


def _stories(session: Session, epic: JiraItem) -> list[JiraItem]:
    """The Stories of an Epic that the orchestrator has seen."""
    return list(
        session.scalars(
            select(JiraItem).where(
                JiraItem.parent_key == epic.key,
                func.lower(JiraItem.type) == STORY_TYPE,
            )
        )
    )


def _unfinished_in_jira(api: JiraApi, epic: JiraItem) -> bool | None:
    """Whether the Epic has a child that is not Done in Jira; None if unreadable."""
    try:
        found = api.search(f"parent = {epic.key} AND statusCategory != Done", 1)
    except OrchestratorError as exc:
        logger.warning("Could not ask Jira about Epic %s: %s", epic.key, exc)
        return None

    return bool(found)


def sync_progress(
    session: Session,
    api: JiraApi | None,
    status_map: dict[str, str],
    set_status: SetStatus,
) -> list[Move]:
    """Move Stories and Epics to the Jira status their work has reached.

    `status_map` is the board-column to Jira-status map. A column the map leaves
    out (set to "-") is not moved to. Returns what was moved.
    """
    moves: list[Move] = []

    def move(item: JiraItem, column: str | None) -> None:
        target = status_map.get(column or "")

        if not target or (item.jira_status or "").casefold() == target.casefold():
            return

        if set_status(item.key, target):
            item.jira_status = target
            session.commit()
            moves.append(Move(item.key, target))

    stories = session.scalars(
        select(JiraItem).where(func.lower(JiraItem.type) == STORY_TYPE)
    )
    columns: dict[str, str | None] = {}

    for story in stories:
        columns[story.key] = story_column(session, story)
        move(story, columns[story.key])

    epics = session.scalars(
        select(JiraItem).where(func.lower(JiraItem.type) == EPIC_TYPE)
    )

    for epic in epics:
        known = _stories(session, epic)
        states = [columns.get(story.key) for story in known]

        if not any(states):
            continue

        if all(state == DONE for state in states):
            # Stories not built by the orchestrator count too: Jira knows them.
            if api is not None and _unfinished_in_jira(api, epic) is False:
                move(epic, DONE)
                continue

        move(epic, BUILDING)

    return moves
