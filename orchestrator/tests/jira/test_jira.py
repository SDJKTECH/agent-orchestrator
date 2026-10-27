from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from orchestrator.app.agents.execution_manager import ExecutionManager
from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import IssueSourceError
from orchestrator.app.db.database import Base
from orchestrator.app.db.models import Milestone, Project, Task, TaskStatus
from orchestrator.app.github.board_reporter import make_board
from orchestrator.app.github.intake import Issue, build_issue_source
from orchestrator.app.github.poller import IntakePoller
from orchestrator.app.github.projects import (
    BUILDING,
    DONE,
    FAILED,
    PR_OPEN,
    QUEUED,
    TESTING,
)
from orchestrator.app.github.publisher import pull_request_body
from orchestrator.app.jira.client import (
    JiraApi,
    adf_to_text,
    make_jira_api,
    quote_jql,
    text_to_adf,
)
from orchestrator.app.jira.source import JiraIssueSource, build_jql
from orchestrator.app.jira.tracker import (
    DEFAULT_STATUS_MAP,
    JiraTracker,
    parse_status_map,
)
from orchestrator.tests.helpers import StaticIssueSource, init_repository

DESCRIPTION = {
    "type": "doc",
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Add a parser."}]},
        {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "handle quotes"}],
                        }
                    ],
                }
            ],
        },
    ],
}


class FakeJira:
    """A transport that records calls and answers like Jira."""

    def __init__(self, status="To Do", transitions=None, search=None, fail=False):
        self.status = status
        self.available = (
            [{"id": "11", "name": "Start", "to": {"name": "In Progress"}}]
            if transitions is None
            else transitions
        )
        self.found = search or []
        self.fail = fail
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))

        if self.fail:
            raise IssueSourceError("Jira is unreachable")

        if path.startswith("/search/jql"):
            return {"issues": self.found}

        if path.endswith("?fields=status"):
            return {"fields": {"status": {"name": self.status}}}

        if path.endswith("/transitions") and method == "GET":
            return {"transitions": self.available}

        return None

    def paths(self, method):
        return [path for m, path, _ in self.calls if m == method]


def tracker(fake, **kwargs) -> JiraTracker:
    return JiraTracker(JiraApi("https://x.atlassian.net", "me", "tok", fake), **kwargs)


def jira_task(**fields) -> Task:
    values = {"id": 7, "title": "PROJ-1: t", "jira_key": "PROJ-1", **fields}
    return Task(**values)


def test_adf_descriptions_become_plain_text() -> None:
    assert adf_to_text(DESCRIPTION).strip() == "Add a parser.\n\n- handle quotes"
    assert adf_to_text(None) == ""
    assert adf_to_text("plain") == "plain"


def test_comments_are_sent_as_documents() -> None:
    document = text_to_adf("first line\nsecond\n\nnext paragraph")

    assert document["type"] == "doc"
    assert len(document["content"]) == 2
    assert document["content"][0]["content"][1] == {"type": "hardBreak"}


def test_jql_literals_are_quoted() -> None:
    assert quote_jql('a"b') == '"a\\"b"'


def test_default_jql_picks_the_trigger_status() -> None:
    settings = Settings(
        _env_file=None, jira_project="PROJ", jira_trigger_status="Ready"
    )

    assert build_jql(settings) == (
        'project = "PROJ" AND status = "Ready" ORDER BY created ASC'
    )


def test_jql_override_and_missing_project() -> None:
    assert build_jql(Settings(_env_file=None, jira_jql="labels = agent")) == (
        "labels = agent"
    )

    with pytest.raises(IssueSourceError, match="JIRA_PROJECT"):
        build_jql(Settings(_env_file=None))


def test_missing_credentials_are_named_without_values() -> None:
    settings = Settings(_env_file=None, jira_base_url="https://x.atlassian.net")

    with pytest.raises(IssueSourceError) as error:
        make_jira_api(settings)

    assert "JIRA_EMAIL" in str(error.value)
    assert "JIRA_API_TOKEN" in str(error.value)
    assert "atlassian" not in str(error.value)


def test_source_turns_jira_issues_into_work_items() -> None:
    fake = FakeJira(
        search=[
            {
                "key": "PROJ-5",
                "fields": {
                    "summary": "Parse CSV",
                    "description": DESCRIPTION,
                    "labels": ["backend"],
                    "fixVersions": [{"name": "M1 - Core"}],
                    "project": {"name": "CSV Summariser"},
                },
            },
            {"key": "PROJ-6", "fields": {"summary": ""}},
        ]
    )
    api = JiraApi("https://x.atlassian.net/", "me", "tok", fake)

    issues = JiraIssueSource(api, "project = PROJ").list_issues()

    assert len(issues) == 1
    issue = issues[0]
    assert issue.jira_key == "PROJ-5"
    assert issue.title == "PROJ-5: Parse CSV"
    assert "https://x.atlassian.net/browse/PROJ-5" in issue.body
    assert "- handle quotes" in issue.body
    assert issue.milestone == "M1 - Core"
    assert issue.board_title == "CSV Summariser"
    assert fake.calls[0][2]["jql"] == "project = PROJ"


def test_status_map_defaults_and_overrides() -> None:
    assert parse_status_map(None) == DEFAULT_STATUS_MAP

    custom = parse_status_map("PR open=Code Review, Done=-, Failed=Blocked")

    assert custom[PR_OPEN] == "Code Review"
    assert DONE not in custom
    assert custom[FAILED] == "Blocked"
    assert custom[BUILDING] == "In Progress"


def test_move_transitions_the_issue_and_remembers_the_column() -> None:
    fake = FakeJira()
    task = jira_task()

    assert tracker(fake).move(task, BUILDING) is True

    assert fake.calls[-1] == (
        "POST",
        "/issue/PROJ-1/transitions",
        {"transition": {"id": "11"}},
    )
    assert fake.paths("POST") == ["/issue/PROJ-1/transitions"]
    assert task.board_column == BUILDING


def test_move_does_nothing_when_the_issue_is_already_there() -> None:
    fake = FakeJira(status="in progress")

    tracker(fake).move(jira_task(), TESTING)

    assert fake.paths("POST") == []


def test_move_survives_a_workflow_without_the_step() -> None:
    fake = FakeJira(transitions=[])
    task = jira_task()

    assert tracker(fake).move(task, BUILDING) is True
    assert fake.paths("POST") == []
    assert task.board_column == BUILDING


def test_move_ignores_tasks_without_a_jira_key_and_repeated_columns() -> None:
    fake = FakeJira()

    assert tracker(fake).move(Task(id=1, title="t"), BUILDING) is False
    assert tracker(fake).move(jira_task(board_column=BUILDING), BUILDING) is False
    assert fake.calls == []


def test_a_jira_outage_never_raises_and_the_column_is_not_recorded() -> None:
    task = jira_task()

    assert tracker(FakeJira(fail=True)).move(task, BUILDING) is False
    assert task.board_column is None


def test_pickup_and_pr_open_are_commented() -> None:
    fake = FakeJira()
    task = jira_task(
        branch_name="agent/task-7",
        pull_request_url="https://gh/pull/3",
        status=TaskStatus.COMPLETED,
    )

    tracker(fake).move(task, QUEUED)
    tracker(fake).move(task, PR_OPEN)

    comments = [body for m, path, body in fake.calls if path.endswith("/comment")]
    assert len(comments) == 2
    assert "agent/task-7" in str(comments[0])
    assert "https://gh/pull/3" in str(comments[1])


def test_merged_pull_requests_finish_the_jira_issue() -> None:
    fake = FakeJira(status="In Review", transitions=[{"id": "31", "name": "Done"}])
    pulls = MagicMock()
    pulls.request.return_value = {"merged": True}
    session_factory = sessionmaker(bind=create_engine("sqlite://"))
    Base.metadata.create_all(bind=session_factory.kw["bind"])

    with session_factory() as session:
        project = Project(name="p", local_path="x")
        milestone = Milestone(project=project, code="GH", title="m")
        task = Task(
            milestone=milestone,
            title="PROJ-1: t",
            jira_key="PROJ-1",
            board_column=PR_OPEN,
            pull_request_url="https://github.com/o/r/pull/3",
        )
        github_task = Task(
            milestone=milestone,
            title="gh",
            board_column=PR_OPEN,
            pull_request_url="https://github.com/o/r/pull/4",
        )
        session.add_all([task, github_task])
        session.commit()

        done = tracker(fake, pulls=pulls).sync_merged(session)

        assert done == [task.id]
        assert task.board_column == DONE
        assert github_task.board_column == PR_OPEN
        pulls.request.assert_called_once_with("GET", "/pulls/3")
        assert "/issue/PROJ-1/transitions" in fake.paths("POST")


def test_poller_imports_a_jira_issue_once_and_tells_jira(tmp_path) -> None:
    repository = tmp_path / "source"
    init_repository(repository)
    engine = create_engine(f"sqlite:///{(tmp_path / 'jira.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    manager = ExecutionManager(max_workers=1)
    ran: list[int] = []
    board = MagicMock()
    source = StaticIssueSource(
        [
            Issue(
                number=0,
                title="PROJ-9: Add stats",
                body="Do it",
                jira_key="PROJ-9",
                board_title="CSV Summariser",
            )
        ]
    )
    poller = IntakePoller(
        settings=Settings(
            _env_file=None, repository_path=repository, poll_interval_seconds=1
        ),
        session_factory=session_factory,
        manager=manager,
        run_pipeline=ran.append,
        source=source,
        board=board,
    )

    poller.poll_once()
    poller.poll_once()
    manager.shutdown(wait=True)
    engine.dispose()

    with session_factory() as session:
        tasks = session.scalars(select(Task)).all()
        assert len(tasks) == 1
        assert tasks[0].jira_key == "PROJ-9"
        assert tasks[0].github_issue_number is None
        assert tasks[0].status == TaskStatus.QUEUED
        assert tasks[0].milestone.project.name == "CSV Summariser"

    board.move.assert_called_once()
    assert board.move.call_args.args[1] == QUEUED


def test_jira_settings_select_the_source_and_the_tracker() -> None:
    settings = Settings(
        _env_file=None,
        issue_source="jira",
        jira_base_url="https://x.atlassian.net",
        jira_email="me@example.com",
        jira_api_token="secret",
        jira_project="PROJ",
    )

    assert isinstance(build_issue_source(settings), JiraIssueSource)
    assert isinstance(make_board(settings), JiraTracker)


def test_pull_request_body_links_the_jira_issue(monkeypatch) -> None:
    monkeypatch.setattr(
        "orchestrator.app.github.publisher.get_settings",
        lambda: Settings(_env_file=None, jira_base_url="https://x.atlassian.net/"),
    )
    task = jira_task(branch_name="agent/task-7")

    assert "Jira: https://x.atlassian.net/browse/PROJ-1" in pull_request_body(task)
