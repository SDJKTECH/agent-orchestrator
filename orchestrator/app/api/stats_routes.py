from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.app.api.schemas import MilestoneStats, StatsResponse
from orchestrator.app.db.database import get_db
from orchestrator.app.db.models import AgentRun, Milestone, Task

router = APIRouter(tags=["stats"])


def _round_cost(value: float | None) -> float:
    # Rounding hides float summation noise (e.g. 0.1 + 0.2).
    return round(value or 0.0, 6)


@router.get("/stats", response_model=StatsResponse)
def get_stats(session: Session = Depends(get_db)) -> StatsResponse:
    """Read-only summary of tasks, agent runs, tokens and cost."""
    status_rows = session.execute(
        select(Task.status, func.count(Task.id)).group_by(Task.status)
    ).all()
    tasks_by_status = {
        status.value: count
        for status, count in sorted(status_rows, key=lambda row: row[0].value)
    }

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
    milestones = session.scalars(
        select(Milestone)
        .where(Milestone.id.in_(task_counts))
        .order_by(Milestone.id)
    ).all()

    return StatsResponse(
        total_tasks=sum(task_counts.values()),
        tasks_by_status=tasks_by_status,
        total_runs=total_runs,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        total_cost_usd=_round_cost(total_cost),
        milestones=[
            MilestoneStats(
                code=milestone.code,
                title=milestone.title,
                task_count=task_counts[milestone.id],
                cost_usd=_round_cost(milestone_costs.get(milestone.id)),
            )
            for milestone in milestones
        ],
    )
