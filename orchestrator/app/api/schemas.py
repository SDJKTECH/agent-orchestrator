# Pydantic request and response models shared by the API routes.
# They define the JSON shapes the API accepts and returns.
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.app.db.models import TaskStatus


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
    github_issue_number: int | None = None
    current_stage: str | None = None
    pull_request_url: str | None = None
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


class MilestoneStats(BaseModel):
    code: str
    title: str
    task_count: int
    cost_usd: float


class StatsResponse(BaseModel):
    total_tasks: int
    tasks_by_status: dict[str, int]
    total_runs: int
    input_tokens: int
    output_tokens: int
    total_cost_usd: float
    milestones: list[MilestoneStats]


class VerificationResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    check_name: str
    passed: bool
    details: str
    created_at: datetime


class TaskDetailResponse(TaskResponse):
    agent_runs: list[AgentRunUsageResponse]
    verification_results: list[VerificationResultResponse]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    repository_url: str | None = Field(default=None, max_length=500)
    local_path: str | None = Field(default=None, max_length=1000)


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    repository_url: str | None
    local_path: str | None
    created_at: datetime


class MilestoneCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


class MilestoneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    code: str
    title: str
    description: str | None
    status: str


class SyncResponse(BaseModel):
    imported: list[TaskResponse]
    skipped_issue_numbers: list[int]
    failed: dict[int, str]


class ControlResponse(BaseModel):
    task_id: int
    status: TaskStatus
    message: str
