# Background thread that checks GitHub for new labelled issues on a timer.
# Imports them and schedules every queued task's pipeline.
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orchestrator.app.agents.execution_manager import ExecutionManager
from orchestrator.app.core.config import Settings
from orchestrator.app.db.models import Milestone, Project, Task, TaskStatus
from orchestrator.app.github.board_reporter import TaskBoard
from orchestrator.app.github.intake import (
    MILESTONE_CODE,
    MILESTONE_TITLE,
    IssueSource,
    build_issue_source,
    sync_issues,
)
from orchestrator.app.github.projects import BLOCKED, QUEUED
from orchestrator.app.github.reporter import GhIssueReporter, picked_up_message
from orchestrator.app.planning.dependencies import (
    Edge,
    assign_blockers,
    open_tasks,
    release_ready,
)
from orchestrator.app.planning.planner import DependencyPlanner

logger = logging.getLogger(__name__)


class IntakePoller:
    """Periodically import labelled GitHub issues and run their pipelines."""

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        manager: ExecutionManager,
        run_pipeline: Callable[[int], None],
        source: IssueSource | None = None,
        reporter: GhIssueReporter | None = None,
        board: TaskBoard | None = None,
        planner: DependencyPlanner | None = None,
    ) -> None:
        if settings.repository_path is None:
            raise ValueError("repository_path is required for issue polling.")

        self.settings = settings
        self.repository_path = Path(settings.repository_path)
        self.session_factory = session_factory
        self.manager = manager
        self.run_pipeline = run_pipeline
        self.source = source
        self.reporter = reporter
        self.board = board
        # Dependencies are worked out for Jira tasks only.
        if (
            planner is None
            and settings.plan_dependencies
            and settings.issue_source == "jira"
        ):
            planner = DependencyPlanner(settings)
        self.planner = planner
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background polling thread."""
        self._thread = threading.Thread(
            target=self._loop, name="intake-poller", daemon=True
        )
        self._thread.start()
        logger.info(
            "Issue polling started: interval_seconds=%s label=%s",
            self.settings.poll_interval_seconds,
            self.settings.issue_label,
        )

    def stop(self) -> None:
        """Ask the polling thread to stop and wait for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        """Poll, then wait for the interval, until stopped. A failed poll is logged."""
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception("Issue poll failed")
            self._stop.wait(self.settings.poll_interval_seconds)

    def poll_once(self) -> list[int]:
        """Import new issues, then schedule every queued task. Return scheduled IDs."""
        source = self.source or build_issue_source(self.settings)

        with self.session_factory() as session:
            milestone_id = self._ensure_milestone(session)
            result = sync_issues(
                session=session,
                milestone_id=milestone_id,
                source=source,
                worktrees_root=self.repository_path.parent / "agent-worktrees",
            )

            edges = self._plan(session, result.imported, result.links)

            for task in result.imported:
                logger.info(
                    "Issue imported: issue=%s task_id=%s",
                    task.github_issue_number,
                    task.id,
                )
                if self.reporter and task.github_issue_number:
                    self.reporter.comment(
                        task.github_issue_number, picked_up_message(task)
                    )
                if self.board:
                    blocked = task.status == TaskStatus.BLOCKED
                    self.board.move(task, BLOCKED if blocked else QUEUED)

            if self.board:
                for edge in edges:
                    if edge.inferred:
                        self.board.link_blocks(edge.blocked, edge.blocker)

            if self.board:
                self.board.sync_merged(session)
                for task in release_ready(
                    session,
                    self.repository_path,
                    self.settings.git_remote,
                    self.settings.base_branch,
                    jira_only=self.settings.issue_source == "jira",
                ):
                    self.board.move(task, QUEUED)

            # Boards create their own projects, all working in this clone.
            queued_ids = list(
                session.scalars(
                    select(Task.id)
                    .join(Milestone, Task.milestone_id == Milestone.id)
                    .join(Project, Milestone.project_id == Project.id)
                    .where(
                        Project.local_path == str(self.repository_path),
                        Task.status == TaskStatus.QUEUED,
                    )
                    .order_by(Task.id)
                )
            )

        return self._schedule(queued_ids)

    def _plan(
        self, session: Session, imported: list[Task], links: dict[int, list[str]]
    ) -> list[Edge]:
        """Work out what the newly imported tasks wait for. Never raises."""
        if not imported or self.board is None:
            return []

        try:
            batch = {
                task.id: task
                for task in open_tasks(
                    session,
                    str(self.repository_path),
                    jira_only=self.settings.issue_source == "jira",
                )
            }
            batch.update({task.id: task for task in imported})
            inferred = (
                self.planner.plan(sorted(batch.values(), key=lambda task: task.id))
                if self.planner and len(batch) > 1
                else {}
            )
            return assign_blockers(session, imported, inferred, links)
        except Exception:
            logger.exception("Dependency planning failed; tasks run independently")
            session.rollback()
            return []

    def _schedule(self, task_ids: list[int]) -> list[int]:
        """Submit each queued task to the thread pool; return the IDs accepted."""
        scheduled: list[int] = []

        for task_id in task_ids:
            try:
                self.manager.submit(
                    task_id, lambda task_id=task_id: self.run_pipeline(task_id)
                )
            except ValueError:
                # Already scheduled or running.
                continue
            scheduled.append(task_id)

        return scheduled

    def _ensure_milestone(self, session: Session) -> int:
        """Find or create the project and milestone that imported tasks join."""
        project_name = self.repository_path.name
        project = session.scalar(select(Project).where(Project.name == project_name))

        if project is None:
            project = Project(
                name=project_name,
                local_path=str(self.repository_path),
                repository_url=self.settings.github_repo,
            )
            session.add(project)
            session.flush()

        milestone = session.scalar(
            select(Milestone).where(
                Milestone.project_id == project.id,
                Milestone.code == MILESTONE_CODE,
            )
        )

        if milestone is None:
            milestone = Milestone(
                project_id=project.id,
                code=MILESTONE_CODE,
                title=MILESTONE_TITLE,
            )
            session.add(milestone)

        session.commit()
        return milestone.id
