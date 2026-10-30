# Pydantic models for the dashboard's overview data.
# Covers stages, branches, usage, milestones and the allowed actions.
from datetime import datetime

from pydantic import BaseModel

from orchestrator.app.api.schemas import AgentRunUsageResponse
from orchestrator.app.db.models import TaskStatus


class StageInfo(BaseModel):
    """State of one pipeline stage (pending, running, done, failed...)."""

    name: str
    label: str
    # pending, running, paused, done, failed, rejected, cancelled or skipped
    state: str
    detail: str | None = None


class BranchInfo(BaseModel):
    """The Git branch of a task and where to see it on GitHub."""

    role: str
    name: str
    url: str | None = None


class DashboardRun(AgentRunUsageResponse):
    """One agent run as the dashboard lists it; the final message loads on demand."""

    process_id: int | None = None
    return_code: int | None = None
    num_turns: int | None = None
    has_result: bool = False


class RunDetail(DashboardRun):
    """Everything known about a single agent run."""

    task_title: str
    jira_key: str | None = None
    result_text: str | None = None


class RoleUsage(BaseModel):
    """What one kind of agent (builder, tester, reviewer) has used so far."""

    role: str
    runs: int
    active: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    seconds: float


class TaskActions(BaseModel):
    """Which control buttons make sense for a task right now."""

    start: bool
    pause: bool
    resume: bool
    cancel: bool


class UsageTotals(BaseModel):
    """Tokens and cost added up for a task or the whole system."""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    cost_usd: float


class DashboardCheck(BaseModel):
    """Latest result of one verification check."""

    check_name: str
    passed: bool
    created_at: datetime


class HierarchyNode(BaseModel):
    """One Jira parent of a task (its Story or Epic)."""

    key: str
    type: str
    title: str
    url: str | None = None


class WorkItem(BaseModel):
    """A Jira issue the orchestrator knows: an Epic, Story or the task's own issue."""

    key: str
    type: str
    title: str
    parent_key: str | None = None
    url: str | None = None
    task_id: int | None = None


class ActivityEvent(BaseModel):
    """Something that happened to a task, for the dashboard's activity feed."""

    at: datetime
    kind: str
    task_id: int
    label: str
    text: str


class DashboardTask(BaseModel):
    """Everything the dashboard shows about one task."""

    id: int
    title: str
    github_issue_number: int | None
    issue_url: str | None
    jira_key: str | None = None
    jira_url: str | None = None
    jira_type: str | None = None
    # The Jira Epic and Story above the task, outermost first.
    hierarchy: list[HierarchyNode] = []
    blocked_by: list[str] = []
    status: TaskStatus
    milestone: str | None
    board_column: str | None = None
    project: str | None = None
    current_stage: str | None
    pull_request_url: str | None
    created_at: datetime
    stages: list[StageInfo]
    branches: list[BranchInfo]
    runs: list[DashboardRun]
    checks: list[DashboardCheck]
    usage: UsageTotals
    actions: TaskActions


class DashboardMilestone(BaseModel):
    """A milestone with its task counts and progress."""

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
    """The numbers in the dashboard's summary tiles."""

    status_counts: dict[str, int]
    active_agents: int
    max_concurrent_agents: int
    usage: UsageTotals
    roles: list[RoleUsage] = []
    polling_enabled: bool


class DashboardOverview(BaseModel):
    """The whole dashboard snapshot returned by GET /dashboard/overview."""

    generated_at: datetime
    repo: str | None
    summary: DashboardSummary
    projects: list[DashboardProject] = []
    milestones: list[DashboardMilestone]
    tasks: list[DashboardTask]
    # Every Jira issue on record (Epics and Stories included), for the Work view.
    work_items: list[WorkItem] = []
    # Newest first.
    activity: list[ActivityEvent] = []
