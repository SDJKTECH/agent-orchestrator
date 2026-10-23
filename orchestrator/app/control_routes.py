from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.database import get_db
from orchestrator.app.models import AgentRun, Task, TaskStatus
from orchestrator.app.process_control import process_control
from orchestrator.app.schemas import AgentRunUsageResponse, ControlResponse

router = APIRouter(tags=["control"])

ACTIVE_RUN_STATUSES = ("STARTING", "RUNNING", "PAUSED")
PAUSABLE = {TaskStatus.STARTING, TaskStatus.RUNNING, TaskStatus.VERIFYING}
CANCELLABLE = PAUSABLE | {TaskStatus.QUEUED, TaskStatus.PAUSED}


def _get_task_or_404(session: Session, task_id: int) -> Task:
    task = session.get(Task, task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID {task_id} does not exist.",
        )

    return task


def _require_status(task: Task, allowed: set[TaskStatus], action: str) -> None:
    if task.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot {action} task {task.id} in status {task.status.value}.",
        )


def _active_runs(session: Session, task_id: int) -> list[AgentRun]:
    return list(
        session.scalars(
            select(AgentRun).where(
                AgentRun.task_id == task_id,
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        )
    )


@router.post("/tasks/{task_id}/pause", response_model=ControlResponse)
def pause_task_endpoint(
    task_id: int,
    session: Session = Depends(get_db),
) -> ControlResponse:
    task = _get_task_or_404(session, task_id)
    _require_status(task, PAUSABLE, "pause")

    process_control.pause(task_id)
    task.status = TaskStatus.PAUSED

    for run in _active_runs(session, task_id):
        run.status = "PAUSED"

    session.commit()
    return ControlResponse(task_id=task_id, status=task.status, message="Task paused.")


@router.post("/tasks/{task_id}/resume", response_model=ControlResponse)
def resume_task_endpoint(
    task_id: int,
    session: Session = Depends(get_db),
) -> ControlResponse:
    task = _get_task_or_404(session, task_id)
    _require_status(task, {TaskStatus.PAUSED}, "resume")

    process_control.resume(task_id)
    task.status = TaskStatus.RUNNING

    for run in _active_runs(session, task_id):
        if run.status == "PAUSED":
            run.status = "RUNNING"

    session.commit()
    return ControlResponse(task_id=task_id, status=task.status, message="Task resumed.")


@router.post("/tasks/{task_id}/cancel", response_model=ControlResponse)
def cancel_task_endpoint(
    task_id: int,
    session: Session = Depends(get_db),
) -> ControlResponse:
    task = _get_task_or_404(session, task_id)
    _require_status(task, CANCELLABLE, "cancel")

    process_control.cancel(task_id)
    task.status = TaskStatus.CANCELLED

    for run in _active_runs(session, task_id):
        run.status = "CANCELLED"

    session.commit()
    return ControlResponse(
        task_id=task_id, status=task.status, message="Task cancelled."
    )


@router.get("/agents/active", response_model=list[AgentRunUsageResponse])
def list_active_agents_endpoint(
    session: Session = Depends(get_db),
) -> list[AgentRun]:
    return list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.status.in_(ACTIVE_RUN_STATUSES))
            .order_by(AgentRun.id)
        )
    )
