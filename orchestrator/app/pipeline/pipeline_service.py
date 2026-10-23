import json
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from orchestrator.app.agents.agent_service import AgentService
from orchestrator.app.agents.process_control import ProcessControl, process_control
from orchestrator.app.db.models import Task, TaskStatus
from orchestrator.app.github.publisher import PullRequestPublisher
from orchestrator.app.pipeline.stage_isolation import StageIsolation

logger = logging.getLogger(__name__)

PIPELINE_STAGES = ("builder", "tester", "reviewer")

# How much of a failure report is passed back to the builder.
MAX_FEEDBACK_CHARS = 3000

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


@dataclass
class _Attempt:
    """How one pass through the stages ended.

    `status` is set when the pipeline must stop. Otherwise `problem` describes
    what failed, and the builder may be asked to fix it.
    """

    status: TaskStatus | None = None
    problem: str | None = None


@dataclass
class _Chain:
    """Branches produced so far, carried from one attempt to the next."""

    tip: str | None = None
    branches: dict[str, str] = field(default_factory=dict)
    pushed: dict[str, bool] = field(default_factory=dict)


class PipelineService:
    """Run a task through the builder, tester and reviewer agents in order.

    When the tester, the verification checks or the reviewer find a problem, the
    builder gets the report and fixes it, then the stages run again. This repeats
    up to `max_fix_attempts` times before the task is marked failed.
    """

    def __init__(
        self,
        db: Session,
        agent_service: AgentService | None = None,
        control: ProcessControl | None = None,
        isolation: StageIsolation | None = None,
        publisher: PullRequestPublisher | None = None,
        max_fix_attempts: int = 0,
    ) -> None:
        self.db = db
        self.control = control or process_control
        self.agents = agent_service or AgentService(db=db, control=self.control)
        self.isolation = isolation
        self.publisher = publisher
        self.max_fix_attempts = max_fix_attempts

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
        chain = _Chain()
        problem: str | None = None

        for attempt in range(1, self.max_fix_attempts + 2):
            if attempt > 1:
                logger.info(
                    "Sending task back to the builder: task_id=%s attempt=%s",
                    task.id,
                    attempt,
                )
                task.status = TaskStatus.RUNNING
                self.db.commit()

            outcome = self._run_attempt(task, attempt, problem, chain)

            if outcome.status is None:
                problem = outcome.problem
                continue

            if outcome.status == TaskStatus.COMPLETED:
                self._open_pull_request(task, chain.branches, chain.pushed)

            return self._finish(task, outcome.status)

        return self._finish(task, TaskStatus.FAILED)

    def _run_attempt(
        self, task: Task, attempt: int, problem: str | None, chain: _Chain
    ) -> _Attempt:
        previous_branch = chain.tip

        for role in PIPELINE_STAGES:
            if not self.control.wait_while_paused(task.id):
                return _Attempt(status=TaskStatus.CANCELLED)

            task.current_stage = role
            self.db.commit()

            workspace = (
                self.isolation.prepare(task, role, previous_branch, attempt)
                if self.isolation
                else None
            )

            stage = self.agents.execute_stage(
                task,
                role,
                self._build_prompt(task, role, problem if attempt > 1 else None),
                worktree_path=workspace.worktree_path if workspace else None,
            )

            if stage.cancelled:
                return _Attempt(status=TaskStatus.CANCELLED)

            if not stage.succeeded:
                logger.warning(
                    "Pipeline stage failed: task_id=%s stage=%s status=%s",
                    task.id,
                    role,
                    stage.agent_run.status,
                )
                if role == "builder":
                    return _Attempt(status=TaskStatus.FAILED)

                return _Attempt(
                    problem=f"The {role} agent did not finish.\n"
                    + _tail(stage.result.stderr or stage.result.stdout)
                )

            if self.isolation and workspace:
                outcome = self.isolation.finish(
                    workspace, f"{role}: {task.title.strip()} (task {task.id})"
                )
                previous_branch = workspace.branch
                chain.branches[role] = workspace.branch
                chain.pushed[role] = outcome.pushed

                if role != "reviewer":
                    chain.tip = workspace.branch

            if role == "tester":
                passed = self.agents.run_verification(task, stage.worktree_path)

                if self.control.is_cancelled(task.id):
                    return _Attempt(status=TaskStatus.CANCELLED)

                if not passed:
                    return _Attempt(problem=self._verification_problem())

            if role == "reviewer":
                verdict = parse_verdict(stage.result.stdout)
                logger.info("Reviewer verdict: task_id=%s verdict=%s", task.id, verdict)

                if verdict != "APPROVE":
                    return _Attempt(
                        problem="The reviewer did not approve the change.\n"
                        + _tail(extract_result_text(stage.result.stdout))
                    )

        return _Attempt(status=TaskStatus.COMPLETED)

    def _verification_problem(self) -> str:
        failures = self.agents.last_failed_checks
        report = "\n\n".join(
            f"[{name}]\n{_tail(details)}" for name, details in failures
        )
        return "These verification checks failed:\n" + report

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
    def _build_prompt(task: Task, role: str, problem: str | None = None) -> str:
        title = task.title.strip()
        description = (task.description or "").strip() or (
            "No additional description provided."
        )
        header = f"Task: {title}\n\nDescription:\n{description}\n\n"

        if role == "builder" and problem:
            return (
                "You are the builder. Your earlier work on the following task was "
                "checked and a problem was found. Your earlier work is on this "
                "branch (committed).\n\n"
                + header
                + "Problem report:\n"
                + problem
                + "\n\nInspect the current code with `git log`, `git status` and "
                "`git diff main...HEAD`, then fix the problem with the smallest "
                "change that resolves it. Fix test files too if the report shows "
                "they fail because of your change. Do not commit or push changes."
            )

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


def _tail(text: str | None) -> str:
    """Keep the end of a report, where tools print the failure."""
    return (text or "").strip()[-MAX_FEEDBACK_CHARS:]
