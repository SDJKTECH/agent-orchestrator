from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.app.models import TaskStatus


class TaskCreate(BaseModel):
    milestone_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=250)
    description: str = Field(default="", max_length=10000)


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    milestone_id: int
    title: str
    description: str
    status: TaskStatus
    branch_name: str | None
    worktree_path: str | None
    created_at: datetime


class AgentRunUsageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    agent_role: str
    model: str | None
    effort: str | None
    status: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    reported_cost_usd: float | None
    estimated_cost: float | None
    session_id: str | None
    started_at: datetime | None
    finished_at: datetime | None


class UsageSummaryResponse(BaseModel):
    task_count: int
    run_count: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    reported_cost_usd: float
    estimated_cost: float
    reported_cost_run_count: int
    estimated_cost_run_count: int
