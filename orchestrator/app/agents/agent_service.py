# Runs one agent stage for a task and records its run, tokens and cost.
# Also runs the verification checks and stores their results.
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from orchestrator.app.agents.agent_runner import (
    AgentExecutionResult,
    ProcessRunner,
    SubprocessRunner,
    build_claude_command,
    resolve_executable,
)
from orchestrator.app.agents.model_router import ModelRouter
from orchestrator.app.agents.process_control import ProcessControl, process_control
from orchestrator.app.agents.usage_tracker import parse_usage
from orchestrator.app.core.config import get_settings
from orchestrator.app.core.exceptions import AgentExecutionError
from orchestrator.app.db.models import AgentRun, Task, TaskStatus
from orchestrator.app.verification.runner import VerificationRunner
from orchestrator.app.verification.service import record_verification_result

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 20000


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(UTC)


@dataclass
class StageResult:
    """Outcome of one agent process executed for a task."""

    agent_run: AgentRun
    result: AgentExecutionResult
    worktree_path: Path

    @property
    def succeeded(self) -> bool:
        return self.agent_run.status == "COMPLETED"

    @property
    def cancelled(self) -> bool:
        return self.agent_run.status == "CANCELLED"


class AgentService:
    """Manage agent execution and synchronize its state with the database."""

    def __init__(
        self,
        db: Session,
        runner: ProcessRunner | None = None,
        verification_runner: VerificationRunner | None = None,
        control: ProcessControl | None = None,
    ) -> None:
        self.db = db
        self.control = control or process_control
        self.runner = runner or SubprocessRunner(control=self.control)
        self.settings = get_settings()
        # (check name, output) for each check that failed in the last verification.
        self.last_failed_checks: list[tuple[str, str]] = []
        self.model_router = ModelRouter(self.settings)
        self.verification_runner = verification_runner or VerificationRunner(
            timeout_seconds=self.settings.verification_timeout_seconds,
            ruff_target=self.settings.verification_ruff_target,
        )

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
        task = self.load_runnable_task(task_id)

        stage = self.execute_stage(
            task,
            agent_role,
            self._build_prompt(task),
            model=model,
            effort=effort,
            max_budget_usd=max_budget_usd,
        )
        agent_run = stage.agent_run

        if not stage.succeeded:
            return agent_run

        if self.run_verification(task, stage.worktree_path):
            task.status = TaskStatus.COMPLETED
        else:
            agent_run.status = "FAILED"
            task.status = TaskStatus.FAILED

        self.db.commit()
        self.db.refresh(agent_run)
        return agent_run

    def load_runnable_task(self, task_id: int) -> Task:
        """Return a task that is allowed to start running."""
        task = self.db.get(Task, task_id)

        if task is None:
            raise ValueError(f"Task with ID {task_id} does not exist.")

        if task.status not in {TaskStatus.QUEUED, TaskStatus.PAUSED}:
            raise ValueError(
                f"Task {task_id} cannot run from status {task.status.value}."
            )

        return task

    def run_verification(self, task: Task, worktree_path: Path) -> bool:
        """Run the verification gate and persist each check result."""
        task.status = TaskStatus.VERIFYING
        self.db.commit()

        verification_results = self.verification_runner.run_checks(worktree_path)
        verification_passed = bool(verification_results)
        self.last_failed_checks = []

        for verification_result in verification_results:
            record_verification_result(
                session=self.db,
                task_id=task.id,
                check_name=verification_result.check_name,
                passed=verification_result.passed,
                details=verification_result.details,
            )

            if not verification_result.passed:
                verification_passed = False
                self.last_failed_checks.append(
                    (verification_result.check_name, verification_result.details)
                )

        return verification_passed

    def execute_stage(
        self,
        task: Task,
        agent_role: str,
        prompt: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        max_budget_usd: float | None = None,
        worktree_path: Path | None = None,
    ) -> StageResult:
        """Run one agent process for a task and record its run and usage.

        A successful stage leaves the task RUNNING; failures and cancellations
        are written to the task as well. The agent runs in worktree_path when
        given, otherwise in the task's own worktree.
        """
        task_id = task.id

        if worktree_path is None:
            if not task.worktree_path:
                raise AgentExecutionError(
                    f"Task {task_id} does not have an assigned worktree."
                )
            worktree_path = Path(task.worktree_path)

        worktree_path = worktree_path.resolve()

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
            role=agent_role,
            definition=agent_definition,
        )
        model = routing_decision.model
        effort = routing_decision.effort

        budget = max_budget_usd
        if budget is None:
            budget = self.settings.max_task_cost_usd

        command = build_claude_command(
            claude_command=resolve_executable(self.settings.claude_command),
            prompt=prompt,
            model=model,
            effort=effort,
            max_budget_usd=budget,
            agent=agent_role,
            permission_mode=getattr(self.settings, "claude_permission_mode", None),
            allowed_tools=[
                tool.strip()
                for tool in (
                    getattr(self.settings, "claude_allowed_tools", "") or ""
                ).split(",")
                if tool.strip()
            ],
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
            logger.info(
                "Agent execution started: task_id=%s run_id=%s "
                "role=%s model=%s effort=%s",
                task.id,
                agent_run.id,
                agent_role,
                model,
                effort,
            )
        except Exception as exc:
            self.db.rollback()
            raise AgentExecutionError(
                f"Could not persist the initial state for task {task_id}."
            ) from exc

        try:
            execution_started = time.perf_counter()
            task.status = TaskStatus.RUNNING
            agent_run.status = "RUNNING"
            self.db.commit()

            result = self.runner.run(
                command,
                cwd=worktree_path,
                timeout_seconds=self.settings.agent_timeout_seconds,
                task_id=task.id,
            )

            usage = parse_usage(result.stdout)

            if usage.input_tokens is not None:
                agent_run.input_tokens = usage.input_tokens

            if usage.output_tokens is not None:
                agent_run.output_tokens = usage.output_tokens

            if usage.cache_creation_input_tokens is not None:
                agent_run.cache_creation_input_tokens = (
                    usage.cache_creation_input_tokens
                )

            if usage.cache_read_input_tokens is not None:
                agent_run.cache_read_input_tokens = usage.cache_read_input_tokens

            if usage.reported_cost_usd is not None:
                agent_run.reported_cost_usd = usage.reported_cost_usd

            if usage.session_id is not None:
                agent_run.session_id = usage.session_id

            if usage.result_text is not None:
                agent_run.result_text = usage.result_text[:MAX_RESULT_CHARS]

            if usage.num_turns is not None:
                agent_run.num_turns = usage.num_turns

            agent_run.process_id = result.process_id
            agent_run.return_code = result.return_code

            if self.control.is_cancelled(task.id):
                agent_run.status = "CANCELLED"
                task.status = TaskStatus.CANCELLED
            elif result.return_code == 0:
                agent_run.status = "COMPLETED"
            else:
                agent_run.status = "FAILED"
                task.status = TaskStatus.FAILED

            agent_run.finished_at = _utc_now()
            self.db.commit()
            self.db.refresh(agent_run)
            logger.info(
                "Agent execution finished: task_id=%s run_id=%s status=%s "
                "return_code=%s duration_seconds=%.3f",
                task.id,
                agent_run.id,
                agent_run.status,
                result.return_code,
                time.perf_counter() - execution_started,
            )
            return StageResult(
                agent_run=agent_run,
                result=result,
                worktree_path=worktree_path,
            )

        except AgentExecutionError:
            logger.error(
                "Agent execution failed: task_id=%s run_id=%s duration_seconds=%.3f",
                task.id,
                agent_run.id,
                time.perf_counter() - execution_started,
            )
            self._mark_failed(task, agent_run)
            raise
        except Exception as exc:
            logger.error(
                "Unexpected error during agent execution: "
                "task_id=%s run_id=%s duration_seconds=%.3f",
                task.id,
                agent_run.id,
                time.perf_counter() - execution_started,
            )
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
