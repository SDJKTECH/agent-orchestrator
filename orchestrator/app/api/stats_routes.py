from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.app.api.schemas import MilestoneStats, StatsResponse
from orchestrator.app.db.database import get_db
from orchestrator.app.db.models import AgentRun, Milestone, Task

router = APIRouter(tags=["stats"])


def _round_cost(value: float) -> float:
    return round(value, 6)


@router.get("/stats", response_model=StatsResponse)
def get_stats(session: Session = Depends(get_db)) -> StatsResponse:
    """Read-only summary of tasks, agent runs, tokens and cost."""
    status_rows = session.execute(
        select(Task.status, func.count(Task.id)).group_by(Task.status)
    ).all()
    tasks_by_status = {status.value: count for status, count in status_rows}

    total_runs, input_tokens, output_tokens, total_cost = session.execute(
        select(
            func.count(AgentRun.id),
            func.coalesce(func.sum(AgentRun.input_tokens), 0),
            func.coalesce(func.sum(AgentRun.output_tokens), 0),
            func.coalesce(func.sum(AgentRun.reported_cost_usd), 0.0),
        )
    ).one()

    task_counts = dict(
        session.execute(
            select(Task.milestone_id, func.count(Task.id)).group_by(Task.milestone_id)
        ).all()
    )
    milestone_costs = dict(
        session.execute(
            select(
                Task.milestone_id,
                func.coalesce(func.sum(AgentRun.reported_cost_usd), 0.0),
            )
            .join(AgentRun, AgentRun.task_id == Task.id)
            .group_by(Task.milestone_id)
        ).all()
    )

    milestones = [
        MilestoneStats(
            code=milestone.code,
            title=milestone.title,
            task_count=task_counts.get(milestone.id, 0),
            cost_usd=_round_cost(milestone_costs.get(milestone.id, 0.0)),
        )
        for milestone in session.scalars(select(Milestone).order_by(Milestone.id))
    ]

    return StatsResponse(
        total_tasks=sum(tasks_by_status.values()),
        tasks_by_status=tasks_by_status,
        total_runs=total_runs,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        total_cost_usd=_round_cost(float(total_cost)),
        milestones=milestones,
    )
