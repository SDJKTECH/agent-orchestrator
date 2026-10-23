import json
import logging
import re

from sqlalchemy.orm import Session

from orchestrator.app.agent_service import AgentService
from orchestrator.app.github_publisher import PullRequestPublisher
from orchestrator.app.models import Task, TaskStatus
from orchestrator.app.process_control import ProcessControl, process_control
from orchestrator.app.stage_isolation import StageIsolation

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
        isolation: StageIsolation | None = None,
        publisher: PullRequestPublisher | None = None,
    ) -> None:
        self.db = db
        self.control = control or process_control
        self.agents = agent_service or AgentService(db=db, control=self.control)
        self.isolation = isolation
        self.publisher = publisher

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
        previous_branch: str | None = None
        pushed_branches: dict[str, bool] = {}
        branches: dict[str, str] = {}

        for role in PIPELINE_STAGES:
            if not self.control.wait_while_paused(task.id):
                return self._finish(task, TaskStatus.CANCELLED)

            task.current_stage = role
            self.db.commit()

            workspace = (
                self.isolation.prepare(task, role, previous_branch)
                if self.isolation
                else None
            )

            stage = self.agents.execute_stage(
                task,
                role,
                self._build_prompt(task, role),
                worktree_path=workspace.worktree_path if workspace else None,
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

            if self.isolation and workspace:
                outcome = self.isolation.finish(
                    workspace, f"{role}: {task.title.strip()} (task {task.id})"
                )
                previous_branch = workspace.branch
                branches[role] = workspace.branch
                pushed_branches[role] = outcome.pushed

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

        self._open_pull_request(task, branches, pushed_branches)
        return self._finish(task, TaskStatus.COMPLETED)

    def _open_pull_request(
        self,
        task: Task,
        branches: dict[str, str],
        pushed_branches: dict[str, bool],
    ) -> None:
        """Open a pull request from the tester branch once the reviewer approved."""
        head = branches.get("tester")

        if not self.publisher or not head or not pushed_branches.get("tester"):
            return

        url = self.publisher.open_pull_request(task, head)

        if url:
            task.pull_request_url = url
            self.db.commit()
            logger.info("Pull request opened: task_id=%s url=%s", task.id, url)

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
                "following task; its work is on this branch (committed) and/or "
                "uncommitted in this worktree.\n\n"
                + header
                + "Inspect it with `git log`, `git status`, `git diff HEAD` and "
                "`git diff main...HEAD`, then add "
                "or update automated tests covering the new behavior. Modify test "
                "files only, never application code. Run the test suite and "
                "report the results. Do not commit or push changes."
            )

        return (
            "You are the reviewer. A builder implemented and a tester covered "
            "the following task; their work is on this branch (committed) "
            "and/or uncommitted in this worktree.\n\n"
            + header
            + "Review it using `git log`, `git status`, `git diff HEAD` and "
            "`git diff main...HEAD` (read untracked files directly). Check "
            "correctness against the task, "
            "test adequacy and code quality. Do not modify any files. End your "
            "reply with exactly one final line: `VERDICT: APPROVE` or "
            "`VERDICT: REJECT`, followed by a one-sentence reason."
        )
