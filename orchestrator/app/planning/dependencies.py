# Records which tasks wait for which, and releases them when their blockers merge.
# A released task gets a fresh worktree cut from the merged origin/main.
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.core.exceptions import RepositoryError
from orchestrator.app.db.models import Task, TaskStatus
from orchestrator.app.github.projects import DONE
from orchestrator.app.planning.planner import Dependency, task_label
from orchestrator.app.vcs.git_manager import (
    create_worktree,
    discard_leftover_worktree,
    fetch_remote,
    remove_worktree,
)

logger = logging.getLogger(__name__)

# Statuses of tasks that can still become blockers or be blocked.
OPEN_STATUSES = (
    TaskStatus.QUEUED,
    TaskStatus.BLOCKED,
    TaskStatus.STARTING,
    TaskStatus.RUNNING,
    TaskStatus.VERIFYING,
    TaskStatus.PAUSED,
)


@dataclass(frozen=True)
class Edge:
    """`blocked` waits for `blocker`."""

    blocked: Task
    blocker: Task
    reason: str
    inferred: bool


def get_blockers(task: Task) -> list[dict]:
    """The blocker entries stored on a task (empty if none or unreadable)."""
    try:
        entries = json.loads(task.blocked_by or "[]")
    except json.JSONDecodeError:
        return []

    return [
        entry for entry in entries if isinstance(entry, dict) and "task_id" in entry
    ]


def is_merged(task: Task) -> bool:
    """A task's work is in main: finished, and its pull request (if any) merged."""
    return task.status == TaskStatus.COMPLETED and (
        task.board_column == DONE or not task.pull_request_url
    )


def open_tasks(
    session: Session, local_path: str | None, jira_only: bool = False
) -> list[Task]:
    """Tasks still in flight, or finished with their pull request not yet merged.

    `jira_only` leaves out tasks that did not come from Jira: nothing marks their
    pull requests merged in Jira mode, so they could never stop blocking.
    """
    tasks = session.scalars(select(Task).order_by(Task.id)).all()
    return [
        task
        for task in tasks
        if (task.status in OPEN_STATUSES or (task.status == TaskStatus.COMPLETED))
        and not is_merged(task)
        and (task.jira_key or not jira_only)
        and (
            local_path is None
            or (task.milestone and task.milestone.project.local_path == local_path)
        )
    ]


def _reaches(graph: dict[int, set[int]], start: int, target: int) -> bool:
    """True if `target` can be reached from `start`; used to spot dependency cycles."""
    seen, stack = set(), [start]

    while stack:
        node = stack.pop()

        if node == target:
            return True

        if node not in seen:
            seen.add(node)
            stack.extend(graph.get(node, ()))

    return False


def assign_blockers(
    session: Session,
    imported: list[Task],
    inferred: dict[str, list[Dependency]],
    explicit: dict[int, list[str]],
) -> list[Edge]:
    """Record what each newly imported task waits for; return the accepted edges.

    Explicit Jira links come first, then the planner's. A link to an unknown or
    already merged task is ignored, and so is one that would make a cycle. A task
    with a blocker still to merge becomes BLOCKED.
    """
    everyone = session.scalars(select(Task)).all()
    by_label = {task_label(task): task for task in everyone}
    graph = {
        task.id: {entry["task_id"] for entry in get_blockers(task)} for task in everyone
    }
    edges: list[Edge] = []

    for task in imported:
        wanted = [(key, "linked in Jira", False) for key in explicit.get(task.id, [])]
        wanted += [
            (dep.blocker, dep.reason or "inferred by the planner", True)
            for dep in inferred.get(task_label(task), [])
        ]
        chosen: dict[int, Edge] = {}

        for label, reason, was_inferred in wanted:
            blocker = by_label.get(label)

            if (
                blocker is None
                or blocker.id == task.id
                or blocker.id in chosen
                or is_merged(blocker)
            ):
                continue

            if _reaches(graph, blocker.id, task.id):
                logger.warning(
                    "Dependency ignored, it would be a cycle: %s waits for %s",
                    task_label(task),
                    label,
                )
                continue

            chosen[blocker.id] = Edge(task, blocker, reason, was_inferred)
            graph.setdefault(task.id, set()).add(blocker.id)

        if chosen:
            task.blocked_by = json.dumps(
                [
                    {"task_id": edge.blocker.id, "reason": edge.reason}
                    for edge in chosen.values()
                ]
            )
            task.status = TaskStatus.BLOCKED
            edges.extend(chosen.values())

    session.commit()
    return edges


def describe_blockers(session: Session, task: Task) -> str:
    """The task's blockers as text, e.g. 'SG-1 (uses its engine)'."""
    parts = []

    for entry in get_blockers(task):
        blocker = session.get(Task, entry["task_id"])
        name = task_label(blocker) if blocker else f"task {entry['task_id']}"
        parts.append(f"{name} ({entry['reason']})" if entry.get("reason") else name)

    return ", ".join(parts)


def release_ready(
    session: Session,
    repository_path: Path,
    remote: str,
    base_branch: str,
    jira_only: bool = False,
) -> list[Task]:
    """Queue every BLOCKED task whose blockers are all merged.

    With `jira_only`, a blocker that is not a Jira task is not waited for.

    Its placeholder worktree (cut from an older main) is replaced by one cut from
    the up-to-date remote branch, so the task sees the blockers' code.
    """
    waiting = [
        task
        for task in session.scalars(
            select(Task).where(Task.status == TaskStatus.BLOCKED).order_by(Task.id)
        )
        if all(
            (blocker := session.get(Task, entry["task_id"])) is None
            or is_merged(blocker)
            or (jira_only and not blocker.jira_key)
            for entry in get_blockers(task)
        )
    ]

    if not waiting:
        return []

    try:
        fetch_remote(repository_path, remote)
    except RepositoryError as exc:
        logger.warning(
            "Could not fetch %s; blocked tasks stay blocked: %s", remote, exc
        )
        return []

    released: list[Task] = []

    for task in waiting:
        name = f"task-{task.id}"
        root = Path(task.worktree_path).parent if task.worktree_path else None

        try:
            if root is not None:
                try:
                    remove_worktree(
                        repository_path,
                        root,
                        name,
                        delete_branch=True,
                        force_branch=True,
                    )
                except RepositoryError:
                    # An earlier attempt may have half-removed it; start clean.
                    discard_leftover_worktree(repository_path, root, name)

                create_worktree(
                    repository_path, root, name, base_branch=f"{remote}/{base_branch}"
                )
        except RepositoryError as exc:
            logger.warning("Could not release task %s: %s", task.id, exc)
            continue

        task.status = TaskStatus.QUEUED
        released.append(task)
        logger.info("Task released, its blockers are merged: task_id=%s", task.id)

    session.commit()
    return released
