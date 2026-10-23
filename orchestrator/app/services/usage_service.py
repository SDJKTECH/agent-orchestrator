from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.db.models import AgentRun, Milestone, Task


def get_task_usage(session: Session, task_id: int) -> dict:
    """Return aggregate usage for all agent runs belonging to a task."""
    task = session.get(Task, task_id)
    if task is None:
        raise ValueError(f"Task with ID {task_id} does not exist.")

    return _aggregate_usage(
        session,
        select(AgentRun).where(AgentRun.task_id == task_id),
        task_count=1,
    )


def get_milestone_usage(session: Session, milestone_id: int) -> dict:
    """Return aggregate usage for all tasks in a milestone."""
    milestone = session.get(Milestone, milestone_id)
    if milestone is None:
        raise ValueError(f"Milestone with ID {milestone_id} does not exist.")

    return _aggregate_usage(
        session,
        select(AgentRun)
        .join(Task, AgentRun.task_id == Task.id)
        .where(Task.milestone_id == milestone_id),
        task_count=len(milestone.tasks),
    )


def _aggregate_usage(
    session: Session,
    query,
    *,
    task_count: int,
) -> dict:
    """Aggregate token counts and costs, treating unavailable values as zero."""
    runs = session.scalars(query).all()

    return {
        "task_count": task_count,
        "run_count": len(runs),
        "input_tokens": sum(run.input_tokens or 0 for run in runs),
        "output_tokens": sum(run.output_tokens or 0 for run in runs),
        "cache_creation_input_tokens": sum(
            run.cache_creation_input_tokens or 0 for run in runs
        ),
        "cache_read_input_tokens": sum(
            run.cache_read_input_tokens or 0 for run in runs
        ),
        "reported_cost_usd": sum(run.reported_cost_usd or 0.0 for run in runs),
        "estimated_cost": sum(run.estimated_cost or 0.0 for run in runs),
        "reported_cost_run_count": sum(
            run.reported_cost_usd is not None for run in runs
        ),
        "estimated_cost_run_count": sum(run.estimated_cost is not None for run in runs),
    }
