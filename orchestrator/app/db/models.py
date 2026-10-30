# ORM models for projects, milestones, tasks, agent runs and check results.
# Also defines the task statuses and the groups of statuses used by controls.
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orchestrator.app.db.database import Base


def utc_now() -> datetime:
    """The current time in UTC, used as a column default."""
    return datetime.now(UTC)


class TaskStatus(str, Enum):
    """Where a task is in its life: queued, blocked, running, completed, failed..."""

    QUEUED = "queued"
    BLOCKED = "blocked"
    STARTING = "starting"
    RUNNING = "running"
    VERIFYING = "verifying"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Which statuses allow which control action (shared by the API and dashboard).
PAUSABLE_STATUSES = {TaskStatus.STARTING, TaskStatus.RUNNING, TaskStatus.VERIFYING}
CANCELLABLE_STATUSES = PAUSABLE_STATUSES | {
    TaskStatus.QUEUED,
    TaskStatus.BLOCKED,
    TaskStatus.PAUSED,
}
FINISHED_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class Project(Base):
    """A repository the orchestrator works on."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    repository_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    local_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    milestones: Mapped[list["Milestone"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )


class Milestone(Base):
    """A group of tasks inside a project."""

    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_milestone_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="planned")

    project: Mapped["Project"] = relationship(back_populates="milestones")
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="milestone",
        cascade="all, delete-orphan",
    )


class Task(Base):
    """One unit of work, built on its own branch and worktree."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    milestone_id: Mapped[int] = mapped_column(ForeignKey("milestones.id"), index=True)
    title: Mapped[str] = mapped_column(String(250))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(
            TaskStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=TaskStatus.QUEUED,
        index=True,
    )
    github_issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(250), nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pull_request_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # GitHub Projects card of the issue, and the board column it was last moved to.
    project_item_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    board_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    board_column: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Key of the Jira issue the task came from (for example "PROJ-123").
    jira_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # The Jira issue record (with its Story and Epic above it); see jira/models.py.
    jira_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("jira_items.id"), nullable=True, index=True
    )
    # Tasks that must be merged first: JSON list of {"task_id": N, "reason": "..."}.
    blocked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    milestone: Mapped["Milestone"] = relationship(back_populates="tasks")
    jira_item: Mapped["JiraItem | None"] = relationship()
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )
    verification_results: Mapped[list["VerificationResult"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )


class AgentRun(Base):
    """One run of a builder, tester or reviewer agent, with its usage and result."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    agent_role: Mapped[str] = mapped_column(String(50), default="builder")
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="STARTING", index=True)
    process_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_input_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    cache_read_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reported_cost_usd: Mapped[float | None] = mapped_column(nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(nullable=True)
    # What the agent finished with: its final message, exit code and turn count.
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    return_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    task: Mapped["Task"] = relationship(back_populates="agent_runs")


class VerificationResult(Base):
    """Outcome of one verification check (pytest, ruff) for a task."""

    __tablename__ = "verification_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    check_name: Mapped[str] = mapped_column(String(150))
    passed: Mapped[bool]
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    task: Mapped["Task"] = relationship(back_populates="verification_results")


# Registered here so create_all builds the Jira tables with the rest.
from orchestrator.app.jira.models import JiraItem  # noqa: E402, F401
