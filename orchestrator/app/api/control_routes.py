# HTTP endpoints to pause, resume and cancel a task, and list active agents.
# They act on the in-memory process registry of this API process.
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.agents.process_control import process_control
from orchestrator.app.api.schemas import AgentRunUsageResponse, ControlResponse
from orchestrator.app.boards import make_board
from orchestrator.app.core.config import get_settings
from orchestrator.app.db.database import get_db
from orchestrator.app.db.models import (
    CANCELLABLE_STATUSES,
    PAUSABLE_STATUSES,
    AgentRun,
    Task,
    TaskStatus,
)
from orchestrator.app.github.projects import FAILED

logger = logging.getLogger(__name__)
router = APIRouter(tags=["control"])

ACTIVE_RUN_STATUSES = ("STARTING", "RUNNING", "PAUSED")


def report_cancelled(task: Task) -> None:
    """Show a cancelled task on its board or Jira issue; never fails the cancel."""
    try:
        board = make_board(get_settings())

        if board is not None:
            board.move(task, FAILED)
    except Exception:
        logger.warning(
            "Could not show the cancel on the board: task_id=%s", task.id, exc_info=True
        )


def _get_task_or_404(session: Session, task_id: int) -> Task:
    """Load the task, or answer 404 if it does not exist."""
    task = session.get(Task, task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID {task_id} does not exist.",
        )

    return task


def _require_status(task: Task, allowed: set[TaskStatus], action: str) -> None:
    """Answer 409 unless the task is in a status that allows the action."""
    if task.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot {action} task {task.id} in status {task.status.value}.",
        )


def _active_runs(session: Session, task_id: int) -> list[AgentRun]:
    """The task's agent runs that have not finished yet."""
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
    """Pause a running task by suspending its agent process."""
    task = _get_task_or_404(session, task_id)
    _require_status(task, PAUSABLE_STATUSES, "pause")

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
    """Resume a paused task."""
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
    """Cancel a task and stop its agent process."""
    task = _get_task_or_404(session, task_id)
    _require_status(task, CANCELLABLE_STATUSES, "cancel")

    # A live pipeline reports the cancel itself; with none left, report it here.
    orphaned = not process_control.has_process(task_id)
    process_control.cancel(task_id)
    task.status = TaskStatus.CANCELLED

    for run in _active_runs(session, task_id):
        run.status = "CANCELLED"

    session.commit()

    if orphaned:
        report_cancelled(task)

    return ControlResponse(
        task_id=task_id, status=task.status, message="Task cancelled."
    )


@router.get("/agents/active", response_model=list[AgentRunUsageResponse])
def list_active_agents_endpoint(
    session: Session = Depends(get_db),
) -> list[AgentRun]:
    """List the agent runs that are running right now."""
    return list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.status.in_(ACTIVE_RUN_STATUSES))
            .order_by(AgentRun.id)
        )
    )
