# Groups the Tasks of a Jira Story onto one Git branch: one pull request per Story.
# Each Task still runs on its own branch (agent/task-N). When it passes review its
# branch is merged into the Story's branch (agent/story-<key>); once every Task of the
# Story is there, a single pull request is opened for the Story.
import logging
import re
import threading
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.core.config import get_settings
from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.db.models import Task, TaskStatus
from orchestrator.app.github.publisher import PullRequestPublisher
from orchestrator.app.jira.models import JiraItem
from orchestrator.app.vcs.git_manager import (
    ensure_branch_worktree,
    merge_branch,
    push_branch,
)

logger = logging.getLogger(__name__)

STORY_TYPE = "story"

# Tasks finish on several threads; the Story branch is merged into one at a time.
_story_lock = threading.Lock()


def story_item(task: Task) -> JiraItem | None:
    """The Jira Story this task belongs to, if its issue sits under one."""
    item = task.jira_item
    parent = item.parent if item is not None else None

    if parent is not None and parent.type.casefold() == STORY_TYPE:
        return parent

    return None


def story_name(item: JiraItem) -> str:
    """The worktree name of a Story's branch, e.g. 'story-sg-2'."""
    return "story-" + re.sub(r"[^A-Za-z0-9_-]+", "-", item.key.lower()).strip("-")


def story_branch_name(item: JiraItem) -> str:
    """The Git branch a Story's Tasks are merged into, e.g. 'agent/story-sg-2'."""
    return f"agent/{story_name(item)}"


def story_tasks(session: Session, item: JiraItem) -> list[Task]:
    """Every orchestrator task built for the Story's issues."""
    return list(
        session.scalars(
            select(Task)
            .join(JiraItem, Task.jira_item_id == JiraItem.id)
            .where(JiraItem.parent_key == item.key)
            .order_by(Task.id)
        )
    )


def story_done(session: Session, item: JiraItem, branch: str) -> bool:
    """True when every Task of the Story is in its branch (cancelled ones aside)."""
    tasks = [
        task
        for task in story_tasks(session, item)
        if task.status != TaskStatus.CANCELLED
    ]
    return bool(tasks) and all(task.story_branch == branch for task in tasks)


def _repository(task: Task) -> Path:
    """The repository clone the task's branch lives in."""
    return Path(task.milestone.project.local_path)


def prepare_story_branch(task: Task, base_branch: str) -> str | None:
    """Make sure the task's Story branch exists; return its name, or None.

    A task that waited for others is cut from this branch, so it sees the code of
    the earlier Tasks of its Story.
    """
    item = story_item(task)

    if item is None or not task.worktree_path:
        return None

    try:
        ensure_branch_worktree(
            _repository(task),
            Path(task.worktree_path).parent,
            story_name(item),
            base_branch,
        )
    except OrchestratorError as exc:
        logger.warning("Could not prepare the Story branch: %s error=%s", item.key, exc)
        return None

    item.branch_name = story_branch_name(item)
    return item.branch_name


def blocker_in_story(blocker: Task, task: Task) -> bool:
    """True if `blocker`'s code is already in the Story branch `task` belongs to."""
    item = story_item(task)

    return (
        item is not None
        and blocker.status == TaskStatus.COMPLETED
        and blocker.story_branch == story_branch_name(item)
    )


def story_pull_request_body(session: Session, item: JiraItem) -> str:
    """The text of a Story's pull request: its Tasks and where they came from."""
    lines = [
        f"Story {item.key}: {item.summary}",
        "",
        "Automated change produced by the agent orchestrator. Each Task below was "
        "built on its own branch and approved by the reviewer agent, then merged "
        "into this Story's branch.",
        "",
    ]

    for task in story_tasks(session, item):
        if task.status != TaskStatus.CANCELLED:
            lines.append(f"- {task.jira_key}: {task.title} (task {task.id})")

    base = get_settings().jira_base_url
    link = f"{base.rstrip('/')}/browse/{item.key}" if base else item.key
    lines.extend(["", f"Jira: {link}", "", "Nothing has been merged."])
    return "\n".join(lines)


class StoryDelivery:
    """Merges finished Tasks into their Story's branch and opens the Story's PR."""

    def __init__(
        self,
        db: Session,
        publisher: PullRequestPublisher | None,
        base_branch: str = "main",
        remote: str = "origin",
        publish: bool = False,
    ) -> None:
        self.db = db
        self.publisher = publisher
        self.base_branch = base_branch
        self.remote = remote
        self.publish = publish

    def deliver(self, task: Task, item: JiraItem) -> bool:
        """Merge the approved task into its Story's branch; open the PR if complete.

        Returns False when the work could not be delivered, so the task must not
        look finished.
        """
        branch = story_branch_name(item)

        with _story_lock:
            try:
                worktree = ensure_branch_worktree(
                    _repository(task),
                    Path(task.worktree_path or "").parent,
                    story_name(item),
                    self.base_branch,
                )
            except OrchestratorError as exc:
                logger.error("No Story branch for %s: %s", item.key, exc)
                return False

            message = (
                f"Merge {task.jira_key or f'task {task.id}'}: {task.title.strip()}"
            )

            if not merge_branch(worktree, task.branch_name or "", message):
                logger.error(
                    "Task conflicts with its Story branch: task_id=%s story=%s",
                    task.id,
                    item.key,
                )
                return False

            if self.publish:
                try:
                    push_branch(worktree, branch, self.remote)
                except OrchestratorError as exc:
                    logger.error("Could not push the Story branch %s: %s", branch, exc)
                    return False

            item.branch_name = branch
            task.story_branch = branch
            self.db.commit()
            logger.info(
                "Task merged into its Story: task_id=%s branch=%s", task.id, branch
            )

            if item.pull_request_url is None and story_done(self.db, item, branch):
                self._open_pull_request(item, task, branch)

            return True

    def _open_pull_request(self, item: JiraItem, task: Task, branch: str) -> None:
        """Open the Story's pull request and show it on each of its tasks."""
        if self.publisher is None:
            return

        url = self.publisher.open_pull_request(
            task,
            branch,
            title=f"{item.key}: {item.summary}",
            body=story_pull_request_body(self.db, item),
        )

        if not url:
            logger.error("Could not open the Story's pull request: story=%s", item.key)
            return

        item.pull_request_url = url
        for member in story_tasks(self.db, item):
            if member.story_branch == branch:
                member.pull_request_url = url
        self.db.commit()
        logger.info("Story pull request opened: story=%s url=%s", item.key, url)
