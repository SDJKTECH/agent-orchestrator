import logging
import threading
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orchestrator.app.config import Settings
from orchestrator.app.execution_manager import ExecutionManager
from orchestrator.app.github_intake import IssueSource, build_issue_source, sync_issues
from orchestrator.app.github_reporter import GhIssueReporter, picked_up_message
from orchestrator.app.models import Milestone, Project, Task, TaskStatus

logger = logging.getLogger(__name__)

MILESTONE_CODE = "GH"
MILESTONE_TITLE = "GitHub issues"


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
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
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
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
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

            queued_ids = list(
                session.scalars(
                    select(Task.id)
                    .where(
                        Task.milestone_id == milestone_id,
                        Task.status == TaskStatus.QUEUED,
                    )
                    .order_by(Task.id)
                )
            )

        return self._schedule(queued_ids)

    def _schedule(self, task_ids: list[int]) -> list[int]:
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
