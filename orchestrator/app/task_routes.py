import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.agent_service import AgentService
from orchestrator.app.config import get_settings
from orchestrator.app.database import SessionLocal, get_db
from orchestrator.app.exceptions import RepositoryError
from orchestrator.app.github_publisher import GhPullRequestPublisher
from orchestrator.app.github_reporter import GhIssueReporter, outcome_message
from orchestrator.app.models import Task, TaskStatus
from orchestrator.app.pipeline_service import PipelineService
from orchestrator.app.process_control import process_control
from orchestrator.app.schemas import TaskCreate, TaskDetailResponse, TaskResponse
from orchestrator.app.stage_isolation import GitStageIsolation
from orchestrator.app.task_service import create_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_task_endpoint(
    payload: TaskCreate,
    session: Session = Depends(get_db),
) -> Task:
    settings = get_settings()

    if settings.repository_path is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No repository_path is configured.",
        )

    try:
        return create_task(
            session=session,
            milestone_id=payload.milestone_id,
            title=payload.title,
            description=payload.description,
            worktrees_root=Path(settings.repository_path).parent / "agent-worktrees",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except RepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


def _run_task_in_background(task_id: int) -> None:
    """Execute an agent using a dedicated database session."""
    with SessionLocal() as session:
        service = AgentService(db=session)

        try:
            service.run_task(task_id)
        except Exception:
            # AgentService records execution failures in the database.
            logger.exception("Background execution failed for task %s", task_id)


@router.post(
    "/{task_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
)
def run_task_endpoint(
    task_id: int,
    request: Request,
    session: Session = Depends(get_db),
) -> dict[str, int | str]:
    task = session.get(Task, task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task with ID {task_id} does not exist.",
        )

    manager = request.app.state.execution_manager

    try:
        manager.submit(
            task_id,
            lambda: _run_task_in_background(task_id),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return {
        "task_id": task_id,
        "status": "QUEUED",
        "message": "Agent execution has been scheduled.",
    }


def _run_pipeline_in_background(task_id: int) -> None:
    """Run the builder, tester and reviewer pipeline with its own session."""
    if process_control.is_cancelled(task_id):
        logger.info("Pipeline skipped, task %s was cancelled before start", task_id)
        return

    with SessionLocal() as session:
        try:
            _build_pipeline(session).run(task_id)
        except Exception:
            # Failures are recorded on the task and its agent runs.
            logger.exception("Background pipeline failed for task %s", task_id)
        finally:
            _report_outcome(session, task_id)


def _build_pipeline(session: Session) -> PipelineService:
    """Create a pipeline whose agents each run in their own branch."""
    settings = get_settings()

    publisher = None
    if settings.publish_branches and settings.open_pull_request:
        publisher = GhPullRequestPublisher(
            repository_path=settings.repository_path,
            github_repo=settings.github_repo,
            base_branch=settings.base_branch,
        )

    return PipelineService(
        db=session,
        isolation=GitStageIsolation(
            publish=settings.publish_branches,
            remote=settings.git_remote,
        ),
        publisher=publisher,
    )


def _report_outcome(session: Session, task_id: int) -> None:
    """Comment the result on the task's GitHub issue, if enabled."""
    settings = get_settings()

    if not settings.comment_on_issues:
        return

    try:
        task = session.get(Task, task_id)

        if task is None or task.github_issue_number is None:
            return

        session.refresh(task)
        GhIssueReporter(
            repository_path=settings.repository_path,
            github_repo=settings.github_repo,
        ).comment(task.github_issue_number, outcome_message(task))
    except Exception:
        logger.exception("Could not report outcome for task %s", task_id)


@router.post(
    "/{task_id}/pipeline",
    status_code=status.HTTP_202_ACCEPTED,
)
def run_pipeline_endpoint(
    task_id: int,
    request: Request,
    session: Session = Depends(get_db),
) -> dict[str, int | str]:
    """Schedule the builder -> tester -> reviewer pipeline for a task."""
    task = session.get(Task, task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID {task_id} does not exist.",
        )

    if task.status != TaskStatus.QUEUED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task {task_id} cannot run from status {task.status.value}.",
        )

    manager = request.app.state.execution_manager

    process_control.clear(task_id)

    try:
        manager.submit(task_id, lambda: _run_pipeline_in_background(task_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return {
        "task_id": task_id,
        "status": "QUEUED",
        "message": "Pipeline execution has been scheduled.",
    }


@router.get("", response_model=list[TaskResponse])
def list_tasks_endpoint(
    session: Session = Depends(get_db),
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
) -> list[Task]:
    query = select(Task).order_by(Task.id)

    if task_status is not None:
        query = query.where(Task.status == task_status)

    return list(session.scalars(query))


@router.get("/{task_id}", response_model=TaskDetailResponse)
def get_task_endpoint(
    task_id: int,
    session: Session = Depends(get_db),
) -> Task:
    task = session.get(Task, task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID {task_id} does not exist.",
        )

    return task
