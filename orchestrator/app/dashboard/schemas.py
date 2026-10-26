# Pydantic models for the dashboard's overview data.
# Covers stages, branches, usage, milestones and the allowed actions.
from datetime import datetime

from pydantic import BaseModel

from orchestrator.app.api.schemas import AgentRunUsageResponse
from orchestrator.app.db.models import TaskStatus


class StageInfo(BaseModel):
    name: str
    label: str
    # pending, running, paused, done, failed, rejected, cancelled or skipped
    state: str
    detail: str | None = None


class BranchInfo(BaseModel):
    role: str
    name: str
    url: str | None = None


class TaskActions(BaseModel):
    start: bool
    pause: bool
    resume: bool
    cancel: bool


class UsageTotals(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    cost_usd: float


class DashboardCheck(BaseModel):
    check_name: str
    passed: bool
    created_at: datetime


class DashboardTask(BaseModel):
    id: int
    title: str
    github_issue_number: int | None
    issue_url: str | None
    status: TaskStatus
    milestone: str | None
    board_column: str | None = None
    project: str | None = None
    current_stage: str | None
    pull_request_url: str | None
    created_at: datetime
    stages: list[StageInfo]
    branches: list[BranchInfo]
    runs: list[AgentRunUsageResponse]
    checks: list[DashboardCheck]
    usage: UsageTotals
    actions: TaskActions


class DashboardMilestone(BaseModel):
    id: int
    project: str
    code: str
    title: str
    task_count: int
    completed_count: int
    failed_count: int
    usage: UsageTotals


class DashboardProject(BaseModel):
    """One project (a GitHub Projects board linked to the repository)."""

    name: str
    board_number: int | None
    task_count: int
    active_count: int
    completed_count: int
    failed_count: int
    columns: dict[str, int]
    usage: UsageTotals


class DashboardSummary(BaseModel):
    status_counts: dict[str, int]
    active_agents: int
    max_concurrent_agents: int
    usage: UsageTotals
    polling_enabled: bool


class DashboardOverview(BaseModel):
    generated_at: datetime
    repo: str | None
    summary: DashboardSummary
    projects: list[DashboardProject] = []
    milestones: list[DashboardMilestone]
    tasks: list[DashboardTask]
