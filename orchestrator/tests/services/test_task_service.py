import subprocess

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from orchestrator.app.core.exceptions import RepositoryError
from orchestrator.app.db.database import Base
from orchestrator.app.db.models import Milestone, Project, Task, TaskStatus
from orchestrator.app.services.task_service import create_task


def initialize_repository(path):
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(path)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test User"], check=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True
    )
    (path / "README.md").write_text("Test repository\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def task_environment(tmp_path):
    repository = tmp_path / "source"
    worktrees_root = tmp_path / "worktrees"
    initialize_repository(repository)

    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session() as session:
        project = Project(name="test-project", local_path=str(repository))
        session.add(project)
        session.flush()

        milestone = Milestone(
            project_id=project.id,
            code="M3",
            title="Task lifecycle",
        )
        session.add(milestone)
        session.commit()

        milestone_id = milestone.id

    yield test_session, milestone_id, repository, worktrees_root

    engine.dispose()


def test_create_task_creates_worktree_and_persists_task(task_environment):
    test_session, milestone_id, repository, worktrees_root = task_environment

    with test_session() as session:
        task = create_task(
            session=session,
            milestone_id=milestone_id,
            title="Implement task service",
            description="Create a task and its worktree.",
            worktrees_root=worktrees_root,
        )

        assert task.id is not None
        assert task.status == TaskStatus.QUEUED
        assert task.branch_name == f"agent/task-{task.id}"
        assert task.worktree_path == str(worktrees_root / f"task-{task.id}")
        assert (worktrees_root / f"task-{task.id}" / "README.md").exists()

        saved_task = session.scalar(select(Task).where(Task.id == task.id))
        assert saved_task is not None
        assert saved_task.title == "Implement task service"

    subprocess.run(
        ["git", "-C", str(repository), "worktree", "list"],
        check=True,
        capture_output=True,
        text=True,
    )


def test_create_task_rejects_missing_milestone(task_environment):
    test_session, _, _, worktrees_root = task_environment

    with test_session() as session:
        with pytest.raises(ValueError, match="does not exist"):
            create_task(
                session=session,
                milestone_id=99999,
                title="Invalid task",
                worktrees_root=worktrees_root,
            )


def test_create_task_rejects_empty_title(task_environment):
    test_session, milestone_id, _, worktrees_root = task_environment

    with test_session() as session:
        with pytest.raises(ValueError, match="title cannot be empty"):
            create_task(
                session=session,
                milestone_id=milestone_id,
                title="   ",
                worktrees_root=worktrees_root,
            )


def test_create_task_rejects_project_without_repository(task_environment):
    test_session, milestone_id, _, worktrees_root = task_environment

    with test_session() as session:
        project = session.get(Milestone, milestone_id).project
        project.local_path = None
        session.commit()

        with pytest.raises(ValueError, match="local repository path"):
            create_task(
                session=session,
                milestone_id=milestone_id,
                title="Task without repository",
                worktrees_root=worktrees_root,
            )


def test_create_task_rolls_back_if_worktree_creation_fails(task_environment):
    test_session, milestone_id, _, worktrees_root = task_environment

    with test_session() as session:
        with pytest.raises(RepositoryError):
            create_task(
                session=session,
                milestone_id=milestone_id,
                title="Task with invalid base branch",
                worktrees_root=worktrees_root,
                base_branch="branch-that-does-not-exist",
            )

        tasks = session.scalars(select(Task)).all()
        assert tasks == []
