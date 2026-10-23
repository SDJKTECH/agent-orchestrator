import subprocess
from threading import Event
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from orchestrator.app.agents.execution_manager import ExecutionManager
from orchestrator.app.core.config import Settings
from orchestrator.app.db.database import Base
from orchestrator.app.db.models import (
    AgentRun,
    Milestone,
    Project,
    Task,
    TaskStatus,
    VerificationResult,
)
from orchestrator.app.github.intake import GhCliIssueSource, Issue
from orchestrator.app.github.poller import IntakePoller
from orchestrator.app.github.reporter import (
    GhIssueReporter,
    outcome_message,
    picked_up_message,
)
from orchestrator.tests.helpers import StaticIssueSource, init_repository


@pytest.fixture
def poller_environment(tmp_path):
    repository = tmp_path / "source"
    init_repository(repository)

    engine = create_engine(f"sqlite:///{(tmp_path / 'poller.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    manager = ExecutionManager(max_workers=2)
    ran: list[int] = []
    done = Event()

    def run_pipeline(task_id: int) -> None:
        ran.append(task_id)
        done.set()

    settings = Settings(
        _env_file=None, repository_path=repository, poll_interval_seconds=1
    )
    source = StaticIssueSource([Issue(number=21, title="Live issue", body="Body")])
    reporter = MagicMock()

    poller = IntakePoller(
        settings=settings,
        session_factory=session_factory,
        manager=manager,
        run_pipeline=run_pipeline,
        source=source,
        reporter=reporter,
    )

    yield poller, session_factory, ran, done, reporter, source

    manager.shutdown()
    engine.dispose()


def test_poll_once_imports_issue_and_runs_pipeline(poller_environment) -> None:
    poller, session_factory, ran, done, reporter, _ = poller_environment

    scheduled = poller.poll_once()

    assert done.wait(timeout=5)
    assert ran == scheduled
    assert len(scheduled) == 1

    with session_factory() as session:
        task = session.scalar(select(Task))
        assert task.github_issue_number == 21
        assert task.title == "Live issue"
        project = session.scalar(select(Project))
        assert project.name == "source"

    reporter.comment.assert_called_once()
    assert reporter.comment.call_args.args[0] == 21


def test_poll_once_is_idempotent_for_known_issues(poller_environment) -> None:
    poller, session_factory, ran, done, reporter, _ = poller_environment
    poller.poll_once()
    assert done.wait(timeout=5)
    with session_factory() as session:
        task = session.scalar(select(Task))
        task.status = TaskStatus.COMPLETED
        session.commit()

    second = poller.poll_once()

    assert second == []
    assert len(ran) == 1
    reporter.comment.assert_called_once()
    with session_factory() as session:
        assert len(session.scalars(select(Task)).all()) == 1
        assert len(session.scalars(select(Milestone)).all()) == 1


def test_poll_once_schedules_leftover_queued_tasks(poller_environment) -> None:
    poller, _, ran, done, _, source = poller_environment
    source.issues = []
    poller.poll_once()

    source.issues = [Issue(number=30, title="Queued before restart")]
    poller.manager.shutdown()
    poller.manager = ExecutionManager(max_workers=1)

    # Simulate a restart: the task exists as QUEUED but nothing is scheduled.
    scheduled = poller.poll_once()

    assert done.wait(timeout=5)
    assert ran == scheduled


def test_poll_once_does_not_double_schedule(poller_environment) -> None:
    poller, _, _, _, _, _ = poller_environment
    release = Event()
    poller.run_pipeline = lambda task_id: release.wait(timeout=5)

    first = poller.poll_once()
    second = poller.poll_once()
    release.set()

    assert len(first) == 1
    assert second == []


def test_poller_requires_repository_path(tmp_path) -> None:
    with pytest.raises(ValueError, match="repository_path"):
        IntakePoller(
            settings=Settings(_env_file=None),
            session_factory=MagicMock(),
            manager=MagicMock(),
            run_pipeline=lambda task_id: None,
        )


def test_gh_source_filters_by_label(tmp_path) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]")

    with patch(
        "orchestrator.app.github.intake.subprocess.run", return_value=completed
    ) as mock_run:
        GhCliIssueSource(repository_path=tmp_path, label="agent-task").list_issues()

    command = mock_run.call_args.args[0]
    assert command[command.index("--label") + 1] == "agent-task"


def test_reporter_comments_through_gh_without_shell(tmp_path) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0)

    with patch(
        "orchestrator.app.github.reporter.subprocess.run", return_value=completed
    ) as mock_run:
        sent = GhIssueReporter(tmp_path, "owner/repo").comment(9, "hello; rm -rf /")

    assert sent is True
    command = mock_run.call_args.args[0]
    assert command[:4] == ["gh", "issue", "comment", "9"]
    assert command[command.index("--body") + 1] == "hello; rm -rf /"
    assert command[command.index("--repo") + 1] == "owner/repo"
    assert "shell" not in mock_run.call_args.kwargs


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError(), subprocess.CalledProcessError(1, ["gh"], stderr="denied")],
)
def test_reporter_swallows_failures(error) -> None:
    with patch("orchestrator.app.github.reporter.subprocess.run", side_effect=error):
        assert GhIssueReporter().comment(1, "text") is False


def test_messages_summarize_task() -> None:
    task = Task(
        id=3,
        title="t",
        status=TaskStatus.COMPLETED,
        branch_name="agent/task-3",
    )
    task.agent_runs = [
        AgentRun(
            agent_role="builder",
            model="sonnet",
            effort="high",
            status="COMPLETED",
            input_tokens=10,
            output_tokens=5,
            reported_cost_usd=0.5,
        )
    ]
    task.verification_results = [
        VerificationResult(check_name="unit_tests", passed=True, details="")
    ]

    assert "task 3" in picked_up_message(task)
    assert "agent/task-3" in picked_up_message(task)

    summary = outcome_message(task)
    assert "completed" in summary
    assert "builder (sonnet/high): COMPLETED, 10 in / 5 out tokens, $0.5000" in summary
    assert "`unit_tests`: passed" in summary
    assert "Total reported cost: $0.5000" in summary
