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


class StageIsolation(Protocol):
    """Gives each pipeline agent its own branch and worktree."""

    def prepare(
        self, task: Task, role: str, previous_branch: str | None
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
        self, task: Task, role: str, previous_branch: str | None
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

        stage_id = f"task-{task.id}-{role}"
        worktree_path = create_worktree(
            repository_path=Path(project.local_path),
            worktrees_root=task_worktree.parent,
            task_id=stage_id,
            base_branch=previous_branch,
        )
        return StageWorkspace(role, f"agent/{stage_id}", worktree_path)

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
