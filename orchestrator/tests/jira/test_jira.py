from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.app.boards import make_board
from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import IssueSourceError
from orchestrator.app.db.database import Base
from orchestrator.app.db.models import Milestone, Project, Task, TaskStatus
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
from orchestrator.app.jira.source import (
    JiraIssueSource,
    build_jql,
    make_jira_source,
    parse_skip_types,
)
from orchestrator.app.jira.tracker import (
    DEFAULT_STATUS_MAP,
    JiraTracker,
    parse_status_map,
)

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

    def __init__(
        self,
        status="To Do",
        transitions=None,
        search=None,
        fail=False,
        issues=None,
        types=None,
    ):
        self.status = status
        self.issues = issues or {}
        self.types = types
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

        if path == "/issuetype":
            return self.types

        if path.startswith("/issue/") and "?fields=summary" in path:
            return self.issues.get(path.split("?")[0].removeprefix("/issue/"))

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

    items = JiraIssueSource(api, "project = PROJ").list_items()

    assert len(items) == 1
    item = items[0]
    assert item.record.key == "PROJ-5"
    assert item.title == "PROJ-5: Parse CSV"
    assert "https://x.atlassian.net/browse/PROJ-5" in item.body
    assert "- handle quotes" in item.body
    assert item.labels == ["backend"]
    assert item.fix_version == "M1 - Core"
    assert item.project_name == "CSV Summariser"
    assert fake.calls[0][2]["jql"] == "project = PROJ"


def hit(key, kind, parent=None, description=None, subtasks=None):
    """A Jira issue payload, as search and GET /issue return it."""
    fields = {"summary": f"{key} summary", "issuetype": {"name": kind}}

    if subtasks is not None:
        fields["subtasks"] = [{"key": subtask} for subtask in subtasks]

    if parent:
        fields["parent"] = {"key": parent}

    if description:
        fields["description"] = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}],
                }
            ],
        }

    return {"key": key, "fields": fields}


def source_for(fake, **kwargs) -> JiraIssueSource:
    api = JiraApi("https://x.atlassian.net", "me", "tok", fake)
    return JiraIssueSource(api, "project = SG", **kwargs)


def test_search_asks_for_type_and_parent() -> None:
    fake = FakeJira()
    source_for(fake).list_items()

    assert {"issuetype", "parent"} <= set(fake.calls[0][2]["fields"])


def test_a_task_gets_its_story_and_epic_outermost_first() -> None:
    fake = FakeJira(
        search=[hit("SG-3", "Task", parent="SG-2")],
        issues={
            "SG-2": hit("SG-2", "Story", parent="SG-1", description="Obstacles"),
            "SG-1": hit("SG-1", "Epic", description="The snake game"),
        },
    )

    [item] = source_for(fake).list_items()

    assert [(a.key, a.type) for a in item.ancestors] == [
        ("SG-1", "Epic"),
        ("SG-2", "Story"),
    ]
    assert item.ancestors[0].description == "The snake game"
    assert item.ancestors[1].parent_key == "SG-1"
    assert item.record.parent_key == "SG-2"


def test_a_task_without_a_parent_has_no_ancestors_and_no_lookup() -> None:
    fake = FakeJira(search=[hit("SG-3", "Task")])

    [item] = source_for(fake).list_items()

    assert item.ancestors == []
    assert fake.paths("GET") == []


def test_a_task_directly_under_an_epic_has_one_ancestor() -> None:
    fake = FakeJira(
        search=[hit("SG-3", "Task", parent="SG-1")],
        issues={"SG-1": hit("SG-1", "Epic")},
    )

    [item] = source_for(fake).list_items()

    assert [a.key for a in item.ancestors] == ["SG-1"]


def test_each_parent_is_read_once_per_search() -> None:
    fake = FakeJira(
        search=[hit("SG-3", "Task", parent="SG-2"), hit("SG-4", "Task", parent="SG-2")],
        issues={"SG-2": hit("SG-2", "Story")},
    )

    items = source_for(fake).list_items()

    assert len(items) == 2
    assert len(fake.paths("GET")) == 1


def test_epics_and_stories_are_not_built() -> None:
    fake = FakeJira(
        search=[
            hit("SG-1", "Epic"),
            hit("SG-2", "Story", parent="SG-1"),
            hit("SG-3", "Task", parent="SG-2"),
            hit("SG-4", "Bug"),
        ],
        issues={"SG-2": hit("SG-2", "Story"), "SG-1": hit("SG-1", "Epic")},
    )

    assert [i.record.key for i in source_for(fake).list_items()] == ["SG-3", "SG-4"]


def test_the_skipped_types_can_be_changed() -> None:
    fake = FakeJira(search=[hit("SG-2", "Story"), hit("SG-3", "Task")])

    only_epics = source_for(fake, skip_types=parse_skip_types("epic")).list_items()
    nothing = source_for(fake, skip_types=parse_skip_types("")).list_items()

    assert [i.record.key for i in only_epics] == ["SG-2", "SG-3"]
    assert len(nothing) == 2
    assert parse_skip_types(" Epic, story ,") == {"epic", "story"}


def test_an_unreadable_parent_ends_the_chain() -> None:
    fake = FakeJira(
        search=[hit("SG-3", "Task", parent="SG-2")],
        issues={"SG-2": hit("SG-2", "Story", parent="SG-1")},
    )

    [item] = source_for(fake).list_items()

    assert [a.key for a in item.ancestors] == ["SG-2"]


def test_a_parent_loop_and_deep_chains_are_cut_off() -> None:
    loop = FakeJira(
        search=[hit("SG-3", "Task", parent="SG-2")],
        issues={
            "SG-2": hit("SG-2", "Story", parent="SG-3"),
        },
    )
    deep = FakeJira(
        search=[hit("K0", "Task", parent="K1")],
        issues={
            f"K{n}": hit(f"K{n}", "Story", parent=f"K{n + 1}") for n in range(1, 9)
        },
    )

    [looped] = source_for(loop).list_items()
    [chain] = source_for(deep).list_items()

    assert [a.key for a in looped.ancestors] == ["SG-2"]
    assert len(chain.ancestors) == 3


def test_a_jira_failure_while_reading_a_parent_is_raised() -> None:
    class Flaky(FakeJira):
        def __call__(self, method, path, body):
            if method == "GET":
                raise IssueSourceError("Jira is unreachable")
            return super().__call__(method, path, body)

    fake = Flaky(search=[hit("SG-3", "Task", parent="SG-2")])

    with pytest.raises(IssueSourceError):
        source_for(fake).list_items()


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


def test_a_failed_or_cancelled_task_goes_back_to_to_do() -> None:
    fake = FakeJira(
        status="In Progress",
        transitions=[{"id": "21", "name": "Reopen", "to": {"name": "To Do"}}],
    )
    task = jira_task(status=TaskStatus.CANCELLED, board_column="Reviewing")

    assert tracker(fake).move(task, FAILED) is True

    assert fake.paths("POST")[0] == "/issue/PROJ-1/transitions"
    assert fake.calls[-2][2] == {"transition": {"id": "21"}}
    assert "cancelled" in str(fake.calls[-1][2])
    assert task.board_column == FAILED


def test_failed_can_be_left_in_place_by_the_status_map() -> None:
    fake = FakeJira(status="In Progress")

    tracker(fake, status_map=parse_status_map("Failed=-")).move(
        jira_task(status=TaskStatus.FAILED), FAILED
    )

    assert fake.paths("POST") == ["/issue/PROJ-1/comment"]


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


def test_jira_settings_select_the_source_and_the_tracker() -> None:
    settings = Settings(
        _env_file=None,
        issue_source="jira",
        jira_base_url="https://x.atlassian.net",
        jira_email="me@example.com",
        jira_api_token="secret",
        jira_project="PROJ",
    )

    assert isinstance(make_jira_source(settings), JiraIssueSource)
    assert isinstance(make_board(settings), JiraTracker)


def test_pull_request_body_links_the_jira_issue(monkeypatch) -> None:
    monkeypatch.setattr(
        "orchestrator.app.github.publisher.get_settings",
        lambda: Settings(_env_file=None, jira_base_url="https://x.atlassian.net/"),
    )
    task = jira_task(branch_name="agent/task-7")

    assert "Jira: https://x.atlassian.net/browse/PROJ-1" in pull_request_body(task)


def test_search_asks_for_subtasks() -> None:
    fake = FakeJira()
    source_for(fake).list_items()

    assert "subtasks" in fake.calls[0][2]["fields"]


def test_an_issue_with_subtasks_is_a_container_even_if_its_type_is_misspelt() -> None:
    # The skip list has a typo ("sto"), as it once did in a real .env.
    fake = FakeJira(
        search=[
            hit("SG-9", "Story", subtasks=["SG-11", "SG-12"]),
            hit("SG-11", "Subtask", parent="SG-9"),
        ],
        issues={"SG-9": hit("SG-9", "Story")},
    )

    items = source_for(fake, skip_types=parse_skip_types("epic,sto")).list_items()

    assert [item.record.key for item in items] == ["SG-11"]


def test_a_plain_task_without_subtasks_is_still_built() -> None:
    fake = FakeJira(search=[hit("SG-20", "Task", subtasks=[])])

    items = source_for(fake, skip_types=parse_skip_types("epic,sto")).list_items()

    assert [item.record.key for item in items] == ["SG-20"]


def test_the_subtask_rule_can_be_turned_off() -> None:
    fake = FakeJira(search=[hit("SG-9", "Story", subtasks=["SG-11"])])

    items = source_for(
        fake, skip_types=parse_skip_types(""), skip_with_subtasks=False
    ).list_items()

    assert [item.record.key for item in items] == ["SG-9"]


def test_records_remember_how_many_subtasks_an_issue_has() -> None:
    from orchestrator.app.jira.source import read_record

    assert read_record(hit("A-1", "Story", subtasks=["A-2", "A-3"])).subtask_count == 2
    assert read_record(hit("A-1", "Task")).subtask_count == 0
    assert (
        read_record(
            {"key": "A-1", "fields": {"summary": "x", "subtasks": None}}
        ).subtask_count
        == 0
    )


def test_skip_rule_settings_default_to_the_safe_choice() -> None:
    settings = Settings(_env_file=None)

    assert settings.jira_skip_types == "Epic,Story"
    assert settings.jira_skip_with_subtasks is True
    assert (
        Settings(_env_file=None, jira_skip_with_subtasks=False).jira_skip_with_subtasks
        is False
    )


REAL_TYPES = [{"name": name} for name in ("Epic", "Story", "Task", "Bug", "Subtask")]


def test_issue_type_names_come_from_jira() -> None:
    api = JiraApi("https://x.atlassian.net", "me", "tok", FakeJira(types=REAL_TYPES))

    assert api.issue_type_names() == {"Epic", "Story", "Task", "Bug", "Subtask"}
    assert (
        JiraApi("https://x.atlassian.net", "me", "tok", FakeJira()).issue_type_names()
        == set()
    )


def test_start_up_check_warns_about_a_skip_type_that_does_not_exist(caplog) -> None:
    from orchestrator.app.jira.source import check_skip_types

    api = JiraApi("https://x.atlassian.net", "me", "tok", FakeJira(types=REAL_TYPES))
    settings = Settings(_env_file=None, jira_skip_types="Epic,Sto")

    with caplog.at_level("INFO", logger="orchestrator.app.jira.source"):
        check_skip_types(settings, api)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "sto" in warnings[0] and "Story" in warnings[0]
    assert any(
        "epic, sto" in r.getMessage() for r in caplog.records if r.levelname == "INFO"
    )


def test_start_up_check_is_quiet_when_the_skip_types_are_right(caplog) -> None:
    from orchestrator.app.jira.source import check_skip_types

    api = JiraApi("https://x.atlassian.net", "me", "tok", FakeJira(types=REAL_TYPES))

    with caplog.at_level("INFO", logger="orchestrator.app.jira.source"):
        check_skip_types(Settings(_env_file=None, jira_skip_types="epic, STORY"), api)

    assert [r for r in caplog.records if r.levelname == "WARNING"] == []
    assert "any issue that has Subtasks" in caplog.text


def test_start_up_check_never_raises_when_jira_is_down(caplog) -> None:
    from orchestrator.app.jira.source import check_skip_types

    api = JiraApi("https://x.atlassian.net", "me", "tok", FakeJira(fail=True))

    with caplog.at_level("INFO", logger="orchestrator.app.jira.source"):
        check_skip_types(Settings(_env_file=None, jira_skip_types="Epic,Sto"), api)

    assert "Could not check JIRA_SKIP_TYPES" in caplog.text
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def test_start_up_check_stays_quiet_if_the_type_list_cannot_be_read(caplog) -> None:
    from orchestrator.app.jira.source import check_skip_types

    api = JiraApi("https://x.atlassian.net", "me", "tok", FakeJira())

    with caplog.at_level("INFO", logger="orchestrator.app.jira.source"):
        check_skip_types(Settings(_env_file=None, jira_skip_types="Sto"), api)

    assert [r for r in caplog.records if r.levelname == "WARNING"] == []
