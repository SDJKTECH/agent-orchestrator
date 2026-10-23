import json
import logging
import re

from sqlalchemy.orm import Session

from orchestrator.app.agent_service import AgentService
from orchestrator.app.models import Task, TaskStatus
from orchestrator.app.process_control import ProcessControl, process_control

logger = logging.getLogger(__name__)

PIPELINE_STAGES = ("builder", "tester", "reviewer")

_VERDICT_PATTERN = re.compile(r"VERDICT:\s*(APPROVE|REJECT)", re.IGNORECASE)


def extract_result_text(stdout: str) -> str:
    """Return the agent's final message from Claude Code JSON output."""
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return stdout or ""

    if isinstance(payload, dict) and isinstance(payload.get("result"), str):
        return payload["result"]

    return stdout or ""


def parse_verdict(stdout: str) -> str | None:
    """Return the reviewer's final APPROVE/REJECT verdict, if one was given."""
    matches = _VERDICT_PATTERN.findall(extract_result_text(stdout))
    return matches[-1].upper() if matches else None


class PipelineService:
    """Run a task through the builder, tester and reviewer agents in order."""

    def __init__(
        self,
        db: Session,
        agent_service: AgentService | None = None,
        control: ProcessControl | None = None,
    ) -> None:
        self.db = db
        self.control = control or process_control
        self.agents = agent_service or AgentService(db=db, control=self.control)

    def run(self, task_id: int) -> Task:
        """Execute every stage, stopping at the first failure or cancellation."""
        task = self.agents.load_runnable_task(task_id)

        try:
            return self._run_stages(task)
        except Exception:
            if task.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                self._finish(task, TaskStatus.FAILED)
            raise

    def _run_stages(self, task: Task) -> Task:
        for role in PIPELINE_STAGES:
            if not self.control.wait_while_paused(task.id):
                return self._finish(task, TaskStatus.CANCELLED)

            task.current_stage = role
            self.db.commit()

            stage = self.agents.execute_stage(
                task, role, self._build_prompt(task, role)
            )

            if stage.cancelled:
                return self._finish(task, TaskStatus.CANCELLED)

            if not stage.succeeded:
                logger.warning(
                    "Pipeline stopped: task_id=%s stage=%s status=%s",
                    task.id,
                    role,
                    stage.agent_run.status,
                )
                return self._finish(task, TaskStatus.FAILED)

            if role == "tester":
                passed = self.agents.run_verification(task, stage.worktree_path)

                if self.control.is_cancelled(task.id):
                    return self._finish(task, TaskStatus.CANCELLED)

                if not passed:
                    return self._finish(task, TaskStatus.FAILED)

            if role == "reviewer":
                verdict = parse_verdict(stage.result.stdout)
                logger.info("Reviewer verdict: task_id=%s verdict=%s", task.id, verdict)

                if verdict != "APPROVE":
                    return self._finish(task, TaskStatus.FAILED)

        return self._finish(task, TaskStatus.COMPLETED)

    def _finish(self, task: Task, status: TaskStatus) -> Task:
        task.status = status
        self.db.commit()
        logger.info(
            "Pipeline finished: task_id=%s status=%s stage=%s",
            task.id,
            status.value,
            task.current_stage,
        )
        return task

    @staticmethod
    def _build_prompt(task: Task, role: str) -> str:
        title = task.title.strip()
        description = (task.description or "").strip() or (
            "No additional description provided."
        )
        header = f"Task: {title}\n\nDescription:\n{description}\n\n"

        if role == "builder":
            return (
                "Complete the following software development task.\n\n"
                + header
                + "Work only within the current repository/worktree. Inspect the "
                "existing code before making changes and implement the task. "
                "Do not write tests; a tester agent handles that next. Do not "
                "commit or push changes."
            )

        if role == "tester":
            return (
                "You are the tester. A builder agent has implemented the "
                "following task; its changes are uncommitted in this worktree.\n\n"
                + header
                + "Inspect the changes with `git status` and `git diff`, then add "
                "or update automated tests covering the new behavior. Modify test "
                "files only, never application code. Run the test suite and "
                "report the results. Do not commit or push changes."
            )

        return (
            "You are the reviewer. A builder implemented and a tester covered "
            "the following task; all changes are uncommitted in this worktree.\n\n"
            + header
            + "Review the changes using `git status` and `git diff HEAD` (read "
            "untracked files directly). Check correctness against the task, "
            "test adequacy and code quality. Do not modify any files. End your "
            "reply with exactly one final line: `VERDICT: APPROVE` or "
            "`VERDICT: REJECT`, followed by a one-sentence reason."
        )
