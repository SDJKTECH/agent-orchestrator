# Lets every agent of a task work in the task's one Git branch and worktree.
# Commits each agent's work as its own commit and pushes the branch when publishing.
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orchestrator.app.core.exceptions import OrchestratorError, RepositoryError
from orchestrator.app.db.models import Task
from orchestrator.app.vcs.git_manager import commit_worktree, push_branch

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageWorkspace:
    """The branch and worktree an agent runs in."""

    role: str
    branch: str
    worktree_path: Path


@dataclass(frozen=True)
class StageOutcome:
    committed: bool
    pushed: bool


class StageIsolation(Protocol):
    """Gives each pipeline agent the branch and worktree it works in."""

    def prepare(self, task: Task, role: str) -> StageWorkspace: ...

    def finish(self, workspace: StageWorkspace, message: str) -> StageOutcome: ...


class GitStageIsolation:
    """One branch per task: the builder, tester and reviewer share it.

    All agents work, one after another, in the task's own branch and worktree
    (agent/task-N). The orchestrator commits after each agent so the history
    shows who changed what, and the next agent starts from that work. Agents
    themselves never commit.
    """

    def __init__(self, publish: bool = False, remote: str = "origin") -> None:
        self.publish = publish
        self.remote = remote

    def prepare(self, task: Task, role: str) -> StageWorkspace:
        if not task.worktree_path or not task.branch_name:
            raise RepositoryError(
                f"Task {task.id} does not have an assigned branch and worktree."
            )

        return StageWorkspace(
            role, task.branch_name, Path(task.worktree_path).resolve()
        )

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
