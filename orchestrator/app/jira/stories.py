# Groups the Tasks of a Jira Story onto one Git branch: one pull request per Story.
# Each Task still runs on its own branch (agent/task-N). When it passes review its
# branch is merged into the Story's branch (agent/story-<key>); once every Task of the
# Story is there, a single pull request is opened for the Story.
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.core.config import Settings, get_settings
from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.db.models import Task, TaskStatus
from orchestrator.app.github.publisher import PullRequestPublisher, make_publisher
from orchestrator.app.jira.client import JiraApi, make_jira_api
from orchestrator.app.jira.models import JiraItem
from orchestrator.app.vcs.git_manager import (
    conflicted_files,
    ensure_branch_worktree,
    merge_branch,
    push_branch,
)

logger = logging.getLogger(__name__)

STORY_TYPE = "story"

# Tasks finish on several threads; the Story branch is merged into one at a time.
_story_lock = threading.Lock()

# Returns the keys of a Story's issues in Jira that are not finished.
OpenChildren = Callable[[JiraItem], set[str]]


@dataclass(frozen=True)
class Delivery:
    """How handing a task over to its Story's branch went.

    `problem` is set when the task conflicts with the Story's branch: the
    conflicts are in the task's worktree, and the builder can resolve them.
    """

    ok: bool
    problem: str | None = None


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


def jira_open_children(api: JiraApi) -> OpenChildren:
    """Look up a Story's unfinished issues in Jira.

    Raises OrchestratorError when Jira cannot be read.
    """

    def open_children(item: JiraItem) -> set[str]:
        found = api.search(f"parent = {item.key} AND statusCategory != Done", 100)
        return {str(issue["key"]) for issue in found if issue.get("key")}

    return open_children


def story_done(
    session: Session,
    item: JiraItem,
    branch: str,
    open_children: OpenChildren | None = None,
) -> bool:
    """True when every Task of the Story is in its branch (cancelled ones aside).

    With `open_children`, Jira is asked as well: a Story's issue that is not yet
    finished in Jira and was not imported yet is still to come, so the Story is
    not done. If Jira cannot be asked, the Story is not called done.
    """
    tasks = [
        task
        for task in story_tasks(session, item)
        if task.status != TaskStatus.CANCELLED
    ]

    if not tasks or not all(task.story_branch == branch for task in tasks):
        return False

    if open_children is None:
        return True

    try:
        coming = open_children(item)
    except OrchestratorError as exc:
        logger.warning("Could not ask Jira about Story %s: %s", item.key, exc)
        return False

    # A cancelled task's issue is not coming either.
    known = {task.jira_key for task in story_tasks(session, item)}
    return not (coming - known)


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


def sync_with_story(task: Task, remote: str, base_branch: str) -> None:
    """Bring the Story's earlier work into a task that is about to start.

    Tasks that do not depend on each other are cut from main, so without this a
    task started after a sibling was delivered would not see the sibling's code
    and could conflict with it later. A conflict is left for delivery to report.
    """
    item = story_item(task)

    if item is None or not task.worktree_path:
        return

    branch = prepare_story_branch(task, f"{remote}/{base_branch}") or (
        prepare_story_branch(task, base_branch)
    )

    if branch is None:
        return

    merge_branch(
        Path(task.worktree_path),
        branch,
        f"Bring {item.key} into task {task.id}",
    )


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
        open_children: OpenChildren | None = None,
    ) -> None:
        self.db = db
        self.publisher = publisher
        self.base_branch = base_branch
        self.remote = remote
        self.publish = publish
        self.open_children = open_children

    def start(self, task: Task) -> None:
        """Bring the Story's earlier work into a task that is about to start."""
        with _story_lock:
            try:
                sync_with_story(task, self.remote, self.base_branch)
            except OrchestratorError as exc:
                logger.warning(
                    "Could not sync task %s with its Story: %s", task.id, exc
                )
            self.db.commit()

    def deliver(self, task: Task, item: JiraItem) -> Delivery:
        """Merge the approved task into its Story's branch; open the PR if complete.

        When the task conflicts with work already in the branch, the conflicts are
        put into the task's own worktree and described in `Delivery.problem`, so
        the builder can resolve them. Any other failure leaves `ok` False without
        a problem: the task must not look finished.
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
                return Delivery(False)

            message = (
                f"Merge {task.jira_key or f'task {task.id}'}: {task.title.strip()}"
            )

            if not merge_branch(worktree, task.branch_name or "", message):
                logger.warning(
                    "Task conflicts with its Story branch: task_id=%s story=%s",
                    task.id,
                    item.key,
                )
                return Delivery(False, self._conflict_problem(task, item, branch))

            if self.publish:
                try:
                    push_branch(worktree, branch, self.remote)
                except OrchestratorError as exc:
                    logger.error("Could not push the Story branch %s: %s", branch, exc)
                    return Delivery(False)

            item.branch_name = branch
            task.story_branch = branch
            self.db.commit()
            logger.info(
                "Task merged into its Story: task_id=%s branch=%s", task.id, branch
            )

            self.open_pull_request_if_done(item, task)
            return Delivery(True)

    def open_pull_request_if_done(self, item: JiraItem, task: Task) -> None:
        """Open the Story's pull request once, when all of its Tasks are in."""
        branch = story_branch_name(item)

        if item.pull_request_url is None and story_done(
            self.db, item, branch, self.open_children
        ):
            self._open_pull_request(item, task, branch)

    def _conflict_problem(self, task: Task, item: JiraItem, branch: str) -> str | None:
        """Bring the Story branch into the task, leaving conflicts to resolve.

        Returns the report for the builder, or None if the task's worktree could
        not be set up for it.
        """
        try:
            merge_branch(
                Path(task.worktree_path or ""),
                branch,
                f"Bring {item.key} into task {task.id}",
                keep_conflicts=True,
            )
            files = conflicted_files(Path(task.worktree_path or ""))
        except OrchestratorError as exc:
            logger.error("Could not set up the conflict for task %s: %s", task.id, exc)
            return None

        if not files:
            return None

        listed = "\n".join(f"- {name}" for name in files)
        return (
            f"This task's work conflicts with the other tasks of its Story "
            f"({item.key}), already merged into `{branch}`. The branch has been "
            "merged into this worktree and these files hold unresolved conflict "
            f"markers (<<<<<<<, =======, >>>>>>>):\n{listed}\n\n"
            "Resolve every conflict so that both this task's change and the "
            "Story's earlier work are kept, and remove all markers."
        )

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


def make_story_delivery(db: Session, settings: Settings) -> StoryDelivery:
    """The delivery for the configured settings: publisher and Jira lookup."""
    publisher = None
    if settings.publish_branches and settings.open_pull_request:
        publisher = make_publisher(settings)

    try:
        open_children = jira_open_children(make_jira_api(settings))
    except OrchestratorError:
        open_children = None  # Jira is not configured: go by the tasks we know.

    return StoryDelivery(
        db=db,
        publisher=publisher,
        base_branch=settings.base_branch,
        remote=settings.git_remote,
        publish=settings.publish_branches,
        open_children=open_children,
    )


def open_pending_pull_requests(db: Session, settings: Settings) -> list[str]:
    """Open the pull request of every Story that is ready for it but has none.

    This retries a pull request that failed to open, and picks up a Story whose
    last issue arrived after its other Tasks were delivered. Returns the keys of
    the Stories whose pull request was opened.
    """
    if not (settings.publish_branches and settings.open_pull_request):
        return []

    waiting = [
        item
        for item in db.scalars(
            select(JiraItem).where(
                JiraItem.branch_name.is_not(None),
                JiraItem.pull_request_url.is_(None),
            )
        )
    ]

    if not waiting:
        return []

    delivery = make_story_delivery(db, settings)
    opened: list[str] = []

    for item in waiting:
        delivered = [
            task
            for task in story_tasks(db, item)
            if task.story_branch == item.branch_name
        ]

        if not delivered:
            continue

        delivery.open_pull_request_if_done(item, delivered[-1])

        if item.pull_request_url:
            opened.append(item.key)

    return opened
