# Creates a task together with its Git branch and worktree.
# Validates the input and cleans up if the database save fails.
from pathlib import Path

from sqlalchemy.orm import Session

from orchestrator.app.core.exceptions import RepositoryError
from orchestrator.app.db.models import Milestone, Task, TaskStatus
from orchestrator.app.vcs.git_manager import create_worktree, remove_worktree


def create_task(
    session: Session,
    milestone_id: int,
    title: str,
    description: str = "",
    worktrees_root: Path | None = None,
    base_branch: str = "main",
    github_issue_number: int | None = None,
    project_item_id: str | None = None,
    board_number: int | None = None,
) -> Task:
    """Create a queued task and prepare its dedicated Git worktree."""
    if not title.strip():
        raise ValueError("Task title cannot be empty.")

    if worktrees_root is None:
        raise ValueError("worktrees_root must be provided.")

    milestone = session.get(Milestone, milestone_id)
    if milestone is None:
        raise ValueError(f"Milestone {milestone_id} does not exist.")

    project = milestone.project
    if not project.local_path:
        raise ValueError(
            f"Project {project.id} does not have a configured local repository path."
        )

    task = Task(
        milestone_id=milestone_id,
        title=title.strip(),
        description=description,
        status=TaskStatus.QUEUED,
        github_issue_number=github_issue_number,
        project_item_id=project_item_id,
        board_number=board_number,
    )
    session.add(task)
    session.flush()

    task_id = f"task-{task.id}"
    worktree_path = None

    try:
        worktree_path = create_worktree(
            repository_path=Path(project.local_path),
            worktrees_root=worktrees_root,
            task_id=task_id,
            base_branch=base_branch,
        )

        task.branch_name = f"agent/{task_id}"
        task.worktree_path = str(worktree_path)
        session.commit()
        session.refresh(task)
        return task
    except Exception:
        session.rollback()

        if worktree_path is not None:
            try:
                remove_worktree(
                    repository_path=Path(project.local_path),
                    worktrees_root=worktrees_root,
                    task_id=task_id,
                    delete_branch=True,
                )
            except RepositoryError:
                # Preserve the original failure; cleanup can be retried separately.
                pass

        raise
