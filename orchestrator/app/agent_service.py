from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from orchestrator.app.agent_runner import (
    ProcessRunner,
    SubprocessRunner,
    build_claude_command,
)
from orchestrator.app.config import get_settings
from orchestrator.app.exceptions import AgentExecutionError
from orchestrator.app.model_router import ModelRouter
from orchestrator.app.models import AgentRun, Task, TaskStatus


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(UTC)


class AgentService:
    """Manage agent execution and synchronize its state with the database."""

    def __init__(
        self,
        db: Session,
        runner: ProcessRunner | None = None,
    ) -> None:
        self.db = db
        self.runner = runner or SubprocessRunner()
        self.settings = get_settings()
        self.model_router = ModelRouter(self.settings)

    def run_task(
        self,
        task_id: int,
        *,
        model: str | None = None,
        effort: str | None = None,
        max_budget_usd: float | None = None,
        agent_role: str = "builder",
    ) -> AgentRun:
        """Execute Claude Code for a task in its assigned worktree."""
        task = self.db.get(Task, task_id)

        if task is None:
            raise ValueError(f"Task with ID {task_id} does not exist.")

        if task.status not in {TaskStatus.QUEUED, TaskStatus.PAUSED}:
            raise ValueError(
                f"Task {task_id} cannot run from status {task.status.value}."
            )

        if not task.worktree_path:
            raise AgentExecutionError(
                f"Task {task_id} does not have an assigned worktree."
            )

        worktree_path = Path(task.worktree_path).resolve()

        if not worktree_path.is_dir():
            raise AgentExecutionError(
                f"Task {task_id} worktree does not exist: {worktree_path}"
            )

        if not agent_role.strip():
            raise ValueError("Agent role cannot be empty.")

        agent_definition = worktree_path / ".claude" / "agents" / f"{agent_role}.md"

        if not agent_definition.is_file():
            role_label = agent_role.replace("_", " ").title()
            raise AgentExecutionError(
                f"{role_label} agent definition not found: "
                f"{agent_definition}. Commit the agent definition to the "
                "base branch and recreate the worktree."
            )

        routing_decision = self.model_router.route(
            model=model,
            effort=effort,
        )
        model = routing_decision.model
        effort = routing_decision.effort

        budget = max_budget_usd
        if budget is None:
            budget = self.settings.max_task_cost_usd

        command = build_claude_command(
            claude_command=self.settings.claude_command,
            prompt=self._build_prompt(task),
            model=model,
            effort=effort,
            max_budget_usd=budget,
            agent=agent_role,
        )

        agent_run = AgentRun(
            task_id=task.id,
            agent_role=agent_role,
            model=model,
            effort=effort,
            status="STARTING",
            started_at=_utc_now(),
        )

        task.status = TaskStatus.STARTING
        self.db.add(agent_run)

        try:
            self.db.commit()
            self.db.refresh(agent_run)
        except Exception as exc:
            self.db.rollback()
            raise AgentExecutionError(
                f"Could not persist the initial state for task {task_id}."
            ) from exc

        try:
            task.status = TaskStatus.RUNNING
            agent_run.status = "RUNNING"
            self.db.commit()

            result = self.runner.run(
                command,
                cwd=worktree_path,
                timeout_seconds=self.settings.agent_timeout_seconds,
            )

            agent_run.process_id = result.process_id
            agent_run.finished_at = _utc_now()

            if result.return_code == 0:
                agent_run.status = "COMPLETED"
                task.status = TaskStatus.COMPLETED
            else:
                agent_run.status = "FAILED"
                task.status = TaskStatus.FAILED

            self.db.commit()
            self.db.refresh(agent_run)
            return agent_run

        except AgentExecutionError:
            self._mark_failed(task, agent_run)
            raise
        except Exception as exc:
            self._mark_failed(task, agent_run)
            raise AgentExecutionError(
                f"Unexpected error while executing task {task_id}: {exc}"
            ) from exc

    def _build_prompt(self, task: Task) -> str:
        """Construct the task prompt from its title and description."""
        title = task.title.strip()
        description = (task.description or "").strip()

        return (
            "Complete the following software development task.\n\n"
            f"Task: {title}\n\n"
            "Description:\n"
            f"{description or 'No additional description provided.'}\n\n"
            "Work only within the current repository/worktree. "
            "Inspect the existing code and tests before making changes. "
            "Implement the task, run relevant tests when feasible, and "
            "summarize the changes and test results. Do not commit or push "
            "changes unless explicitly instructed."
        )

    def _mark_failed(self, task: Task, agent_run: AgentRun) -> None:
        """Persist failure state without masking the original exception."""
        task.status = TaskStatus.FAILED
        agent_run.status = "FAILED"
        agent_run.finished_at = _utc_now()

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
