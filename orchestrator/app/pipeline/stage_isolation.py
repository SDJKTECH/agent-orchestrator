import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orchestrator.app.core.exceptions import OrchestratorError, RepositoryError
from orchestrator.app.db.models import Task
from orchestrator.app.vcs.git_manager import (
    commit_worktree,
    create_worktree,
    push_branch,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageWorkspace:
    """The branch and worktree a single agent runs in."""

    role: str
    branch: str
    worktree_path: Path


@dataclass(frozen=True)
class StageOutcome:
    committed: bool
    pushed: bool


def stage_branch(task: Task, role: str, attempt: int = 1) -> str:
    """Name of the branch an agent works in.

    The first builder uses the task's own branch. Later builder attempts (fixes)
    and every other role get `agent/task-N-<role>`, with `-rK` added from the
    second attempt on.
    """
    if role == "builder" and attempt == 1:
        return task.branch_name or f"agent/task-{task.id}"

    suffix = "" if attempt == 1 else f"-r{attempt}"
    return f"agent/task-{task.id}-{role}{suffix}"


class StageIsolation(Protocol):
    """Gives each pipeline agent its own branch and worktree."""

    def prepare(
        self, task: Task, role: str, previous_branch: str | None, attempt: int = 1
    ) -> StageWorkspace: ...

    def finish(self, workspace: StageWorkspace, message: str) -> StageOutcome: ...


class GitStageIsolation:
    """One branch per agent run, each starting from the previous agent's work.

    The builder uses the task's own branch and worktree (agent/task-N). The
    tester and reviewer get agent/task-N-<role> branches created from the
    previous agent's branch. The orchestrator commits each agent's changes so
    the next agent starts from them; agents themselves never commit.
    """

    def __init__(self, publish: bool = False, remote: str = "origin") -> None:
        self.publish = publish
        self.remote = remote

    def prepare(
        self, task: Task, role: str, previous_branch: str | None, attempt: int = 1
    ) -> StageWorkspace:
        if not task.worktree_path or not task.branch_name:
            raise RepositoryError(
                f"Task {task.id} does not have an assigned branch and worktree."
            )

        task_worktree = Path(task.worktree_path)

        if previous_branch is None:
            return StageWorkspace(role, task.branch_name, task_worktree.resolve())

        project = task.milestone.project

        if not project.local_path:
            raise RepositoryError(
                f"Project {project.id} does not have a local repository path."
            )

        branch = stage_branch(task, role, attempt)
        stage_id = branch.removeprefix("agent/")
        worktree_path = create_worktree(
            repository_path=Path(project.local_path),
            worktrees_root=task_worktree.parent,
            task_id=stage_id,
            base_branch=previous_branch,
        )
        return StageWorkspace(role, branch, worktree_path)

    def finish(self, workspace: StageWorkspace, message: str) -> StageOutcome:
        committed = commit_worktree(workspace.worktree_path, message)
        pushed = False

        if self.publish:
            try:
                push_branch(workspace.worktree_path, workspace.branch, self.remote)
                pushed = True
            except OrchestratorError as exc:
                logger.warning(
                    "Could not push branch: branch=%s error=%s",
                    workspace.branch,
                    exc,
                )

        logger.info(
            "Agent branch finished: branch=%s committed=%s pushed=%s",
            workspace.branch,
            committed,
            pushed,
        )
        return StageOutcome(committed=committed, pushed=pushed)
