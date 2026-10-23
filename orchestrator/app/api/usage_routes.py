# HTTP endpoints that report token usage and cost.
# Available per agent run, per task and per milestone.
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from orchestrator.app.api.schemas import (
    AgentRunUsageResponse,
    UsageSummaryResponse,
)
from orchestrator.app.db.database import get_db
from orchestrator.app.db.models import AgentRun
from orchestrator.app.services.usage_service import (
    get_milestone_usage,
    get_task_usage,
)

router = APIRouter(tags=["usage"])


@router.get(
    "/tasks/{task_id}/usage",
    response_model=UsageSummaryResponse,
)
def get_task_usage_endpoint(
    task_id: int,
    session: Session = Depends(get_db),
) -> dict:
    try:
        return get_task_usage(session, task_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/tasks/{task_id}/runs",
    response_model=list[AgentRunUsageResponse],
)
def get_task_runs_endpoint(
    task_id: int,
    session: Session = Depends(get_db),
) -> list[AgentRun]:
    try:
        get_task_usage(session, task_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return list(
        session.query(AgentRun)
        .filter(AgentRun.task_id == task_id)
        .order_by(AgentRun.id)
        .all()
    )


@router.get(
    "/milestones/{milestone_id}/usage",
    response_model=UsageSummaryResponse,
)
def get_milestone_usage_endpoint(
    milestone_id: int,
    session: Session = Depends(get_db),
) -> dict:
    try:
        return get_milestone_usage(session, milestone_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
