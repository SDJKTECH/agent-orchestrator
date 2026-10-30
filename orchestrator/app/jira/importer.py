# Turns the Jira Tasks found by the source into orchestrator tasks.
# A Task and the Story and Epic above it are saved as JiraItem rows; the Task also
# gets a queued orchestrator task with its own Git worktree. Each is imported once.
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.db.models import Milestone, Project, Task
from orchestrator.app.jira.models import JiraItem
from orchestrator.app.jira.source import JiraRecord, JiraWorkItem
from orchestrator.app.services.task_service import create_task

logger = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 250
MAX_DESCRIPTION_LENGTH = 10000
MAX_SUMMARY_LENGTH = 500
MAX_MILESTONE_TITLE_LENGTH = 200
MAX_MILESTONE_CODE_LENGTH = 20
MAX_PROJECT_NAME_LENGTH = 150

# Milestone for Jira Tasks that have no fix version.
MILESTONE_CODE = "JIRA"
MILESTONE_TITLE = "Jira issues"


class JiraItemSource(Protocol):
    """Where Jira work items come from; replaceable in tests."""

    def list_items(self) -> list[JiraWorkItem]: ...


@dataclass
class JiraSyncResult:
    """What one import did: tasks created, keys skipped, failures and Jira links."""

    imported: list[Task] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    # Task id -> Jira keys of the issues that block it.
    links: dict[int, list[str]] = field(default_factory=dict)


def jira_project(session: Session, template: Project, name: str) -> Project:
    """The project for a Jira project name, created on first use.

    Every Jira project works in the same repository clone as `template`.
    """
    name = name.strip()[:MAX_PROJECT_NAME_LENGTH]
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


def default_milestone(session: Session, project_id: int) -> int:
    """The milestone for a project's Tasks that have no fix version."""
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


def milestone_for_version(
    session: Session, project_id: int, fix_version: str | None, default_id: int
) -> int:
    """The milestone named like the Task's Jira fix version, created on first use."""
    if not fix_version:
        return default_id

    title = fix_version[:MAX_MILESTONE_TITLE_LENGTH]
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
        )
        session.add(milestone)
        session.flush()

    return milestone.id


def save_item(session: Session, record: JiraRecord, project_id: int) -> JiraItem:
    """Create the JiraItem for `record`, or bring an existing one up to date.

    The parent must be saved first; a parent that is not saved is left unlinked.
    """
    parent_key = (
        record.parent_key
        if record.parent_key
        and session.scalar(select(JiraItem.id).where(JiraItem.key == record.parent_key))
        else None
    )
    item = session.scalar(select(JiraItem).where(JiraItem.key == record.key))

    if item is None:
        item = JiraItem(key=record.key, project_id=project_id)
        session.add(item)

    item.project_id = project_id
    item.type = record.type
    item.summary = record.summary[:MAX_SUMMARY_LENGTH]
    item.description = record.description
    item.jira_status = record.status
    item.parent_key = parent_key
    session.flush()
    return item


def sync_jira_items(
    session: Session,
    milestone_id: int,
    source: JiraItemSource,
    worktrees_root: Path,
) -> JiraSyncResult:
    """Create a queued task (with worktree) for each Jira Task not yet imported.

    Tasks go to a project named after their Jira project, and into the milestone
    named like their fix version (else the project's default one). Their Story and
    Epic are saved as JiraItems and linked, outermost first.
    """
    items = source.list_items()
    default = session.get(Milestone, milestone_id)
    project_id = default.project_id if default else None
    known = set(
        session.scalars(select(Task.jira_key).where(Task.jira_key.is_not(None)))
    )
    result = JiraSyncResult()

    for work in items:
        key = work.record.key

        if key in known:
            result.skipped.append(key)
            continue

        try:
            target_project, target_default = project_id, milestone_id

            if default is not None and work.project_name:
                project = jira_project(session, default.project, work.project_name)
                target_project = project.id
                target_default = default_milestone(session, project.id)

            for record in work.ancestors:
                save_item(session, record, target_project)

            jira_item = save_item(session, work.record, target_project)
            task = create_task(
                session=session,
                milestone_id=(
                    milestone_for_version(
                        session, target_project, work.fix_version, target_default
                    )
                    if target_project is not None
                    else milestone_id
                ),
                title=work.title[:MAX_TITLE_LENGTH],
                description=work.body[:MAX_DESCRIPTION_LENGTH],
                worktrees_root=worktrees_root,
                jira_key=key,
                jira_item_id=jira_item.id,
            )
        except (OrchestratorError, ValueError) as exc:
            logger.warning("Jira import failed: issue=%s error=%s", key, exc)
            result.failed[key] = str(exc)
            continue

        known.add(key)

        if work.blocked_by:
            result.links[task.id] = list(work.blocked_by)

        result.imported.append(task)

    return result
