import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from orchestrator.app.agent_service import AgentService
from orchestrator.app.config import get_settings
from orchestrator.app.database import SessionLocal, get_db
from orchestrator.app.exceptions import RepositoryError
from orchestrator.app.models import Task
from orchestrator.app.schemas import TaskCreate, TaskResponse
from orchestrator.app.task_service import create_task

router = APIRouter(prefix="/tasks", tags=["tasks"])

logger = logging.getLogger(__name__)


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
            logger.exception(
                "Background execution failed for task %s",
                task_id,
            )


@router.post(
    "/{task_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
)
def run_task_endpoint(
    task_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_db),
) -> dict[str, int | str]:
    task = session.get(Task, task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task with ID {task_id} does not exist.",
        )

    background_tasks.add_task(_run_task_in_background, task_id)

    return {
        "task_id": task_id,
        "status": "QUEUED",
        "message": "Agent execution has been scheduled.",
    }
