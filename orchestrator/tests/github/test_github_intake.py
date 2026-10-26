import json
import subprocess
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import IssueSourceError
from orchestrator.app.db.database import Base
from orchestrator.app.db.models import Milestone, Project, Task
from orchestrator.app.github.intake import (
    FileIssueSource,
    GhCliIssueSource,
    Issue,
    build_issue_source,
    sync_issues,
)
from orchestrator.tests.helpers import StaticIssueSource, init_repository


@pytest.fixture
def intake_environment(tmp_path):
    repository = tmp_path / "source"
    init_repository(repository)

    engine = create_engine(f"sqlite:///{(tmp_path / 'intake.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        project = Project(name="intake", local_path=str(repository))
        session.add(project)
        session.flush()
        milestone = Milestone(project_id=project.id, code="M1", title="Intake")
        session.add(milestone)
        session.commit()
        milestone_id = milestone.id

    yield session_factory, milestone_id, tmp_path / "worktrees"

    engine.dispose()


def test_file_source_reads_gh_style_issues(tmp_path) -> None:
    path = tmp_path / "issues.json"
    path.write_text(
        json.dumps(
            [
                {
                    "number": 7,
                    "title": " Add feature ",
                    "body": None,
                    "labels": [{"name": "bug"}, "plain"],
                }
            ]
        ),
        encoding="utf-8",
    )

    issues = FileIssueSource(path).list_issues()

    assert issues == [
        Issue(number=7, title="Add feature", body="", labels=["bug", "plain"])
    ]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"number": 1}',
        '["text"]',
        '[{"number": "1", "title": "x"}]',
        '[{"number": true, "title": "x"}]',
        '[{"number": 1, "title": "  "}]',
    ],
)
def test_file_source_rejects_invalid_content(tmp_path, content) -> None:
    path = tmp_path / "issues.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(IssueSourceError):
        FileIssueSource(path).list_issues()


def test_file_source_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(IssueSourceError, match="Could not read"):
        FileIssueSource(tmp_path / "missing.json").list_issues()


def test_gh_source_builds_command_without_shell(tmp_path) -> None:
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='[{"number": 3, "title": "From gh"}]'
    )

    with patch(
        "orchestrator.app.github.intake.subprocess.run", return_value=completed
    ) as mock_run:
        issues = GhCliIssueSource(
            repository_path=tmp_path, github_repo="owner/repo", limit=5
        ).list_issues()

    assert [issue.number for issue in issues] == [3]
    command = mock_run.call_args.args[0]
    assert command[:3] == ["gh", "issue", "list"]
    assert command[command.index("--repo") + 1] == "owner/repo"
    assert command[command.index("--limit") + 1] == "5"
    assert mock_run.call_args.kwargs["cwd"] == tmp_path
    assert "shell" not in mock_run.call_args.kwargs


def test_gh_source_reports_missing_cli() -> None:
    with patch(
        "orchestrator.app.github.intake.subprocess.run",
        side_effect=FileNotFoundError(),
    ):
        with pytest.raises(IssueSourceError, match="GitHub CLI"):
            GhCliIssueSource().list_issues()


def test_gh_source_reports_command_failure() -> None:
    error = subprocess.CalledProcessError(1, ["gh"], stderr="not logged in")

    with patch("orchestrator.app.github.intake.subprocess.run", side_effect=error):
        with pytest.raises(IssueSourceError, match="not logged in"):
            GhCliIssueSource().list_issues()


def test_build_issue_source_selects_by_setting(tmp_path) -> None:
    file_settings = Settings(
        _env_file=None, issue_source="file", issue_file_path=tmp_path / "i.json"
    )
    gh_settings = Settings(_env_file=None, issue_source="gh")

    assert isinstance(build_issue_source(file_settings), FileIssueSource)
    assert isinstance(build_issue_source(gh_settings), GhCliIssueSource)

    with pytest.raises(IssueSourceError, match="ISSUE_FILE_PATH"):
        build_issue_source(Settings(_env_file=None, issue_source="file"))


def test_sync_issues_creates_tasks_and_skips_duplicates(intake_environment) -> None:
    session_factory, milestone_id, worktrees_root = intake_environment
    source = StaticIssueSource(
        [Issue(number=11, title="First", body="Do it"), Issue(number=12, title="Two")]
    )

    with session_factory() as session:
        first = sync_issues(session, milestone_id, source, worktrees_root)
        second = sync_issues(session, milestone_id, source, worktrees_root)

        assert [task.github_issue_number for task in first.imported] == [11, 12]
        assert first.skipped == []
        assert second.imported == []
        assert second.skipped == [11, 12]
        assert len(session.scalars(select(Task)).all()) == 2

    task = first.imported[0]
    assert task.description == "Do it"
    assert task.branch_name == f"agent/task-{task.id}"
    assert (worktrees_root / f"task-{task.id}" / "README.md").exists()


def test_sync_issues_truncates_long_fields(intake_environment) -> None:
    session_factory, milestone_id, worktrees_root = intake_environment
    source = StaticIssueSource([Issue(number=1, title="t" * 400, body="b" * 20000)])

    with session_factory() as session:
        result = sync_issues(session, milestone_id, source, worktrees_root)

    assert len(result.imported[0].title) == 250
    assert len(result.imported[0].description) == 10000


def test_sync_issues_reports_failures_and_continues(intake_environment) -> None:
    session_factory, milestone_id, worktrees_root = intake_environment
    source = StaticIssueSource(
        [Issue(number=1, title="   "), Issue(number=2, title="Valid")]
    )

    with session_factory() as session:
        result = sync_issues(session, milestone_id, source, worktrees_root)

    assert list(result.failed) == [1]
    assert [task.github_issue_number for task in result.imported] == [2]


def test_file_source_reads_github_milestones(tmp_path) -> None:
    path = tmp_path / "issues.json"
    path.write_text(
        json.dumps(
            [
                {
                    "number": 1,
                    "title": "a",
                    "milestone": {"title": "M6 - Cost", "number": 8},
                },
                {"number": 2, "title": "b", "milestone": None},
                {"number": 3, "title": "c", "milestone": "M1 - Base"},
            ]
        ),
        encoding="utf-8",
    )

    issues = FileIssueSource(path).list_issues()

    assert [(i.milestone, i.milestone_number) for i in issues] == [
        ("M6 - Cost", 8),
        (None, None),
        ("M1 - Base", None),
    ]


def test_sync_issues_routes_issues_to_their_github_milestone(
    intake_environment,
) -> None:
    session_factory, default_id, worktrees_root = intake_environment
    source = StaticIssueSource(
        [
            Issue(
                number=1, title="a", milestone="M6 - Token and Cost", milestone_number=8
            ),
            Issue(
                number=2, title="b", milestone="M6 - Token and Cost", milestone_number=8
            ),
            Issue(number=3, title="c"),
        ]
    )

    with session_factory() as session:
        sync_issues(session, default_id, source, worktrees_root)
        again = sync_issues(session, default_id, source, worktrees_root)

        by_issue = {
            task.github_issue_number: task.milestone
            for task in session.scalars(select(Task))
        }
        assert by_issue[1] is by_issue[2]
        assert by_issue[1].code == "M6"
        assert by_issue[1].title == "M6 - Token and Cost"
        assert by_issue[1].github_milestone_number == 8
        assert by_issue[3].id == default_id
        assert again.imported == []
        assert sorted(again.skipped) == [1, 2, 3]
        assert len(session.scalars(select(Milestone)).all()) == 2


def test_sync_issues_gives_each_board_its_own_project(intake_environment) -> None:
    session_factory, default_id, worktrees_root = intake_environment
    source = StaticIssueSource(
        [
            Issue(number=1, title="a", board_number=4, board_title="CSV Summariser"),
            Issue(number=2, title="b", board_number=4, board_title="CSV Summariser"),
            Issue(number=3, title="c", board_number=5, board_title="Task Dashboard"),
            Issue(number=4, title="d"),
        ]
    )

    with session_factory() as session:
        sync_issues(session, default_id, source, worktrees_root)
        again = sync_issues(session, default_id, source, worktrees_root)

        tasks = {t.github_issue_number: t for t in session.scalars(select(Task))}
        project = {n: t.milestone.project for n, t in tasks.items()}
        assert project[1] is project[2]
        assert project[1].name == "CSV Summariser"
        assert project[3].name == "Task Dashboard"
        assert project[4].name == "intake"
        # Every board works in the same repository clone.
        assert project[1].local_path == project[4].local_path
        assert [tasks[n].board_number for n in (1, 3, 4)] == [4, 5, None]
        assert again.imported == []
        assert sorted(again.skipped) == [1, 2, 3, 4]
