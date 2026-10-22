from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from orchestrator.app.config import get_settings
from orchestrator.app.database import get_db
from orchestrator.app.exceptions import RepositoryError
from orchestrator.app.models import Task
from orchestrator.app.schemas import TaskCreate, TaskResponse
from orchestrator.app.task_service import create_task

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
