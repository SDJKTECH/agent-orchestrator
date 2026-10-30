# Pydantic request and response models shared by the API routes.
# They define the JSON shapes the API accepts and returns.
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.app.db.models import TaskStatus


class TaskCreate(BaseModel):
    """Request body to create a task."""

    milestone_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=250)
    description: str = Field(default="", max_length=10000)


class TaskResponse(BaseModel):
    """A task as returned by the API."""

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
    """Tokens, cost and timing of one agent run."""

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
    """Tokens and cost added up over several agent runs."""

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
    """Task and cost totals of one milestone."""

    code: str
    title: str
    task_count: int
    cost_usd: float


class StatsResponse(BaseModel):
    """Response of GET /stats: overall totals and per-milestone totals."""

    total_tasks: int
    tasks_by_status: dict[str, int]
    total_runs: int
    input_tokens: int
    output_tokens: int
    total_cost_usd: float
    milestones: list[MilestoneStats]


class VerificationResultResponse(BaseModel):
    """One stored verification check result."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    check_name: str
    passed: bool
    details: str
    created_at: datetime


class VerificationCheckSummary(BaseModel):
    """Pass and fail counts of one kind of check."""

    check_name: str
    passed: int
    failed: int


class VerificationSummaryResponse(BaseModel):
    """Response of GET /verification/summary."""

    total_results: int
    passed: int
    failed: int
    checks: list[VerificationCheckSummary]


class TaskDetailResponse(TaskResponse):
    """A task together with its verification results."""

    agent_runs: list[AgentRunUsageResponse]
    verification_results: list[VerificationResultResponse]


class ProjectCreate(BaseModel):
    """Request body to create a project."""

    name: str = Field(min_length=1, max_length=150)
    repository_url: str | None = Field(default=None, max_length=500)
    local_path: str | None = Field(default=None, max_length=1000)


class ProjectResponse(BaseModel):
    """A project as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    repository_url: str | None
    local_path: str | None
    created_at: datetime


class MilestoneCreate(BaseModel):
    """Request body to create a milestone."""

    code: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


class MilestoneResponse(BaseModel):
    """A milestone as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    code: str
    title: str
    description: str | None
    status: str


class SyncResponse(BaseModel):
    """Result of importing issues: imported, skipped and failed."""

    imported: list[TaskResponse]
    # GitHub issue numbers, or Jira keys such as "SG-3".
    skipped_issue_numbers: list[int | str]
    failed: dict[int | str, str]


class ControlResponse(BaseModel):
    """Result of a pause, resume or cancel request."""

    task_id: int
    status: TaskStatus
    message: str
