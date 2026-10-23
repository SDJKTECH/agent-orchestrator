import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.app.agent_service import AgentService
from orchestrator.app.database import Base
from orchestrator.app.exceptions import RepositoryError
from orchestrator.app.git_manager import commit_worktree, push_branch
from orchestrator.app.github_publisher import (
    GhPullRequestPublisher,
    pull_request_body,
)
from orchestrator.app.models import Milestone, Project, Task, TaskStatus
from orchestrator.app.pipeline_service import PipelineService
from orchestrator.app.process_control import ProcessControl
from orchestrator.app.stage_isolation import GitStageIsolation
from orchestrator.app.task_service import create_task
from orchestrator.app.verification_runner import VerificationRunner
from orchestrator.tests.test_github_intake import init_repository

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_CLAUDE = REPO_ROOT / "demo" / "fake_claude.py"


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def add_sample_project(repository: Path) -> None:
    (repository / "sample_app").mkdir()
    (repository / "tests").mkdir()
    (repository / "sample_app" / "__init__.py").write_text("", encoding="utf-8")
    (repository / "tests" / "test_smoke.py").write_text(
        "def test_smoke() -> None:\n    assert True\n", encoding="utf-8"
    )
    (repository / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["."]\n', encoding="utf-8"
    )
    (repository / ".gitignore").write_text(
        "__pycache__/\n.pytest_cache/\n.ruff_cache/\n", encoding="utf-8"
    )
    shutil_copy_agents(repository)
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "Add sample project")


def shutil_copy_agents(repository: Path) -> None:
    import shutil

    shutil.copytree(REPO_ROOT / ".claude" / "agents", repository / ".claude" / "agents")


@pytest.fixture
def git_environment(tmp_path):
    repository = tmp_path / "source"
    init_repository(repository)
    add_sample_project(repository)

    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    git(repository, "remote", "add", "origin", str(remote))
    git(repository, "push", "origin", "main")

    engine = create_engine(f"sqlite:///{(tmp_path / 'stage.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        project = Project(name="stage", local_path=str(repository))
        session.add(project)
        session.flush()
        milestone = Milestone(project_id=project.id, code="M1", title="Stage")
        session.add(milestone)
        session.commit()
        milestone_id = milestone.id

    yield SimpleNamespace(
        repository=repository,
        remote=remote,
        session_factory=session_factory,
        milestone_id=milestone_id,
        worktrees_root=tmp_path / "worktrees",
    )

    engine.dispose()


def remote_branches(remote: Path) -> set[str]:
    output = git(remote, "branch", "--list")
    return {line.strip().lstrip("* ") for line in output.splitlines()}


def test_commit_worktree_commits_changes_and_reports_nothing_to_do(
    git_environment,
) -> None:
    repository = git_environment.repository
    (repository / "new.txt").write_text("hello\n", encoding="utf-8")

    assert commit_worktree(repository, "Add new file") is True
    assert git(repository, "log", "-1", "--format=%s") == "Add new file"
    assert commit_worktree(repository, "Nothing changed") is False


def test_commit_worktree_rejects_empty_message(git_environment) -> None:
    with pytest.raises(RepositoryError, match="message"):
        commit_worktree(git_environment.repository, "  ")


def test_push_branch_validates_names_and_pushes(git_environment) -> None:
    repository = git_environment.repository

    with pytest.raises(RepositoryError, match="Invalid"):
        push_branch(repository, "--force", "origin")

    git(repository, "checkout", "-q", "-b", "agent/pushed")
    push_branch(repository, "agent/pushed", "origin")

    assert "agent/pushed" in remote_branches(git_environment.remote)


def test_isolation_gives_each_agent_its_own_branch(git_environment) -> None:
    env = git_environment

    with env.session_factory() as session:
        task = create_task(
            session=session,
            milestone_id=env.milestone_id,
            title="Isolated work",
            worktrees_root=env.worktrees_root,
        )
        isolation = GitStageIsolation()

        builder = isolation.prepare(task, "builder", None)
        (builder.worktree_path / "built.txt").write_text("built\n", encoding="utf-8")
        built = isolation.finish(builder, "builder: work")

        tester = isolation.prepare(task, "tester", builder.branch)
        assert (tester.worktree_path / "built.txt").exists()
        (tester.worktree_path / "tested.txt").write_text("tested\n", encoding="utf-8")
        isolation.finish(tester, "tester: work")

        reviewer = isolation.prepare(task, "reviewer", tester.branch)

    assert built.committed is True
    assert built.pushed is False
    assert builder.branch == f"agent/task-{task.id}"
    assert tester.branch == f"agent/task-{task.id}-tester"
    assert reviewer.branch == f"agent/task-{task.id}-reviewer"
    assert (
        len({builder.worktree_path, tester.worktree_path, reviewer.worktree_path}) == 3
    )
    assert (reviewer.worktree_path / "tested.txt").exists()

    # The builder branch never receives the tester's work.
    assert not (builder.worktree_path / "tested.txt").exists()
    assert git(env.repository, "branch", "--list", "agent/task-*").count("agent/") == 3


def test_isolation_publish_pushes_branch_and_survives_push_failure(
    git_environment,
) -> None:
    env = git_environment

    with env.session_factory() as session:
        task = create_task(
            session=session,
            milestone_id=env.milestone_id,
            title="Published work",
            worktrees_root=env.worktrees_root,
        )

    workspace = GitStageIsolation(publish=True).prepare(task, "builder", None)
    (workspace.worktree_path / "file.txt").write_text("x\n", encoding="utf-8")

    pushed = GitStageIsolation(publish=True).finish(workspace, "builder: x")
    assert pushed.pushed is True
    assert workspace.branch in remote_branches(env.remote)

    broken = GitStageIsolation(publish=True, remote="missing-remote").finish(
        workspace, "builder: y"
    )
    assert broken.committed is False
    assert broken.pushed is False


def test_isolation_requires_branch_and_worktree() -> None:
    task = Task(id=1, title="No worktree")

    with pytest.raises(RepositoryError, match="assigned branch"):
        GitStageIsolation().prepare(task, "builder", None)


def test_pipeline_end_to_end_with_real_git_branches(
    git_environment, monkeypatch
) -> None:
    env = git_environment
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "0")

    settings = SimpleNamespace(
        claude_command=f'"{sys.executable}" "{FAKE_CLAUDE}"',
        max_task_cost_usd=None,
        agent_timeout_seconds=120,
        verification_timeout_seconds=300,
        verification_ruff_target=".",
        default_model="sonnet",
        default_effort="medium",
        builder_model=None,
        builder_effort="high",
        tester_model="haiku",
        tester_effort="medium",
        reviewer_model="opus",
        reviewer_effort="high",
    )
    monkeypatch.setattr("orchestrator.app.agent_service.get_settings", lambda: settings)

    publisher = MagicMock()
    publisher.open_pull_request.return_value = "https://github.com/o/r/pull/1"

    with env.session_factory() as session:
        task = create_task(
            session=session,
            milestone_id=env.milestone_id,
            title="Add greeting helper",
            description="Return a greeting.",
            worktrees_root=env.worktrees_root,
            github_issue_number=42,
        )
        control = ProcessControl()
        agents = AgentService(
            db=session,
            verification_runner=VerificationRunner(
                timeout_seconds=300, ruff_target="."
            ),
            control=control,
        )
        pipeline = PipelineService(
            db=session,
            agent_service=agents,
            control=control,
            isolation=GitStageIsolation(publish=True),
            publisher=publisher,
        )

        finished = pipeline.run(task.id)
        task_id = task.id

    assert finished.status == TaskStatus.COMPLETED
    assert finished.pull_request_url == "https://github.com/o/r/pull/1"

    published = remote_branches(env.remote)
    assert {
        f"agent/task-{task_id}",
        f"agent/task-{task_id}-tester",
        f"agent/task-{task_id}-reviewer",
    } <= published

    builder_files = git(
        env.repository, "diff", "--name-only", f"main..agent/task-{task_id}"
    )
    tester_files = git(
        env.repository, "diff", "--name-only", f"main..agent/task-{task_id}-tester"
    )
    assert "sample_app/add_greeting_helper.py" in builder_files
    assert "tests/test_add_greeting_helper.py" not in builder_files
    assert "tests/test_add_greeting_helper.py" in tester_files

    head = publisher.open_pull_request.call_args.args[1]
    assert head == f"agent/task-{task_id}-tester"


def test_publisher_opens_pull_request_through_gh(tmp_path) -> None:
    task = Task(id=7, title="Do it", github_issue_number=3)
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="Creating pull request\nhttps://x/pull/9\n"
    )

    with patch(
        "orchestrator.app.github_publisher.subprocess.run", return_value=completed
    ) as mock_run:
        url = GhPullRequestPublisher(tmp_path, "o/r", "main").open_pull_request(
            task, "agent/task-7-tester"
        )

    assert url == "https://x/pull/9"
    command = mock_run.call_args.args[0]
    assert command[:3] == ["gh", "pr", "create"]
    assert command[command.index("--head") + 1] == "agent/task-7-tester"
    assert command[command.index("--base") + 1] == "main"
    assert command[command.index("--repo") + 1] == "o/r"
    assert "Closes #3" in command[command.index("--body") + 1]
    assert "shell" not in mock_run.call_args.kwargs


def test_publisher_returns_none_when_gh_fails(tmp_path) -> None:
    error = subprocess.CalledProcessError(1, ["gh"], stderr="no permission")

    with patch("orchestrator.app.github_publisher.subprocess.run", side_effect=error):
        assert (
            GhPullRequestPublisher(tmp_path).open_pull_request(
                Task(id=1, title="t"), "branch"
            )
            is None
        )


def test_pull_request_body_omits_close_line_without_issue() -> None:
    assert "Closes" not in pull_request_body(Task(id=1, title="t"))
