import pytest

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import IssueSourceError
from orchestrator.app.github.intake import ProjectIssueSource, build_issue_source
from orchestrator.app.github.projects import (
    BOARD_COLUMNS,
    ProjectsApi,
    discover_boards,
    make_projects_apis,
)

OPTIONS = [{"id": f"opt-{i}", "name": name} for i, name in enumerate(BOARD_COLUMNS)]


def card(item_id, number, column, *, state="OPEN", repo="me/repo", milestone=None):
    return {
        "id": item_id,
        "fieldValueByName": {"name": column} if column else None,
        "content": {
            "number": number,
            "title": f"Issue {number}",
            "body": "body",
            "state": state,
            "repository": {"nameWithOwner": repo},
            "labels": {"nodes": [{"name": "bug"}]},
            "milestone": milestone,
        },
    }


class FakeGraphQL:
    def __init__(self, nodes, options=OPTIONS, errors=None):
        self.nodes = nodes
        self.options = options
        self.errors = errors
        self.requests = []

    def __call__(self, payload):
        self.requests.append(payload)

        if self.errors:
            return {"errors": self.errors}

        if "updateProjectV2ItemFieldValue" in payload["query"]:
            return {"data": {"updateProjectV2ItemFieldValue": {}}}

        root = "user" if "user(login" in payload["query"] else "organization"
        return {
            "data": {
                root: {
                    "projectV2": {
                        "id": "proj-1",
                        "title": "Roadmap",
                        "field": {"id": "field-1", "options": self.options},
                        "items": {"nodes": self.nodes},
                    }
                }
            }
        }


def make_api(transport, owner_type="user"):
    return ProjectsApi("me", 1, "secret", owner_type, transport=transport)


def test_only_open_issues_of_this_repo_in_todo_are_picked_up() -> None:
    transport = FakeGraphQL(
        [
            card("a", 1, "Todo", milestone={"title": "M1 - Start", "number": 4}),
            card("b", 2, "Building"),
            card("c", 3, "Todo", state="CLOSED"),
            card("d", 4, "Todo", repo="me/other"),
            card("e", 5, None),
            {"id": "f", "content": {}},
        ]
    )

    issues = ProjectIssueSource(make_api(transport), "Me/Repo").list_issues()

    assert [issue.number for issue in issues] == [1]
    assert issues[0].project_item_id == "a"
    assert issues[0].milestone == "M1 - Start"
    assert issues[0].milestone_number == 4
    assert issues[0].labels == ["bug"]


def test_a_board_without_our_columns_is_not_worked_from() -> None:
    transport = FakeGraphQL([card("a", 1, "Todo")], options=OPTIONS[:2])

    assert ProjectIssueSource(make_api(transport)).list_issues() == []


def test_set_column_sends_the_option_id_of_the_column() -> None:
    transport = FakeGraphQL([])
    api = make_api(transport)

    api.set_column("item-9", "Testing")

    mutation = transport.requests[-1]
    assert mutation["variables"] == {
        "project": "proj-1",
        "item": "item-9",
        "field": "field-1",
        "option": f"opt-{BOARD_COLUMNS.index('Testing')}",
    }


def test_set_column_rejects_a_column_the_board_lacks() -> None:
    api = make_api(FakeGraphQL([], options=OPTIONS[:2]))

    with pytest.raises(IssueSourceError, match="no 'Testing' column"):
        api.set_column("item", "Testing")

    assert "Testing" in api.missing_columns()


def test_organization_boards_use_the_organization_query() -> None:
    transport = FakeGraphQL([])

    make_api(transport, owner_type="org").load()

    assert "organization(login" in transport.requests[0]["query"]


def test_graphql_errors_are_reported_without_the_token() -> None:
    api = make_api(FakeGraphQL([], errors=[{"message": "Resource not accessible"}]))

    with pytest.raises(IssueSourceError, match="Resource not accessible") as caught:
        api.load()

    assert "secret" not in str(caught.value)


def test_missing_project_gives_a_hint_about_the_token_scope() -> None:
    def empty(payload):
        return {"data": {"user": None}}

    with pytest.raises(IssueSourceError, match="project"):
        make_api(empty).load()


def test_project_config_is_required_for_the_project_source() -> None:
    settings = Settings(_env_file=None, issue_source="project")

    assert make_projects_apis(settings) == []
    with pytest.raises(IssueSourceError, match="PROJECT_TOKEN"):
        build_issue_source(settings)


def test_several_boards_are_read_from_project_numbers() -> None:
    settings = Settings(
        _env_file=None,
        issue_source="project",
        project_owner="me",
        project_numbers="4, 5,4",
        project_number=6,
        github_token="ghp_x",
    )

    assert [api.number for api in make_projects_apis(settings)] == [4, 5, 6]


def test_bad_project_numbers_are_rejected() -> None:
    settings = Settings(
        _env_file=None, project_owner="me", project_numbers="4,x", github_token="t"
    )

    with pytest.raises(IssueSourceError, match="comma-separated"):
        make_projects_apis(settings)


def test_issues_carry_the_title_and_number_of_their_board() -> None:
    api = make_api(FakeGraphQL([card("a", 1, "Todo")]))

    issues = ProjectIssueSource(api).list_issues()

    assert (issues[0].board_number, issues[0].board_title) == (1, "Roadmap")


def linked(*nodes, typename="User"):
    def transport(payload):
        assert payload["variables"] == {"owner": "me", "name": "repo"}
        return {
            "data": {
                "repository": {
                    "owner": {"__typename": typename, "login": "me"},
                    "projectsV2": {"nodes": list(nodes)},
                }
            }
        }

    return transport


def test_boards_linked_to_the_repo_are_discovered_without_numbers() -> None:
    boards = discover_boards(
        "secret",
        "me/repo",
        linked(
            {"number": 4, "title": "To-do App", "closed": False},
            {"number": 5, "title": "CSV Summariser", "closed": False},
            {"number": 7, "title": "Old", "closed": True},
        ),
    )

    assert [(b.number, b.title, b.owner, b.owner_type) for b in boards] == [
        (4, "To-do App", "me", "user"),
        (5, "CSV Summariser", "me", "user"),
    ]


def test_discovery_recognises_organization_owners() -> None:
    boards = discover_boards(
        "secret",
        "me/repo",
        linked({"number": 1, "title": "T", "closed": False}, typename="Organization"),
    )

    assert boards[0].owner_type == "org"


def test_make_projects_apis_uses_the_linked_boards_when_no_numbers_are_set() -> None:
    settings = Settings(
        _env_file=None,
        issue_source="project",
        github_repo="me/repo",
        github_token="ghp_x",
    )

    apis = make_projects_apis(
        settings, linked({"number": 8, "title": "A", "closed": False})
    )

    assert [(api.owner, api.number) for api in apis] == [("me", 8)]


def test_explicit_numbers_override_discovery() -> None:
    def never(payload):
        raise AssertionError("discovery must not run")

    settings = Settings(
        _env_file=None,
        github_repo="me/repo",
        github_token="ghp_x",
        project_numbers="9",
    )

    assert [api.number for api in make_projects_apis(settings, never)] == [9]


def test_discovery_reports_a_missing_repository() -> None:
    with pytest.raises(IssueSourceError, match="not found"):
        discover_boards("secret", "me/repo", lambda payload: {"data": {}})


def test_project_token_falls_back_to_the_github_token() -> None:
    settings = Settings(
        _env_file=None,
        issue_source="project",
        project_owner="me",
        project_number=3,
        github_token="ghp_x",
    )

    apis = make_projects_apis(settings)

    assert [api.number for api in apis] == [3]


DEFAULTS = [
    {"id": "d1", "name": "Todo", "color": "GREEN", "description": ""},
    {"id": "d2", "name": "In Progress", "color": "YELLOW", "description": ""},
    {"id": "d3", "name": "Done", "color": "PURPLE", "description": "finished"},
]


class GrowingBoard:
    """A board whose Status field can be edited, like the real one."""

    def __init__(self, options, nodes=(), reject_ids=False):
        self.options = [dict(option) for option in options]
        self.nodes = list(nodes)
        self.reject_ids = reject_ids
        self.field_updates = []
        self.converted = []

    def __call__(self, payload):
        query, variables = payload["query"], payload["variables"]

        if "updateProjectV2Field" in query:
            sent = variables["options"]
            self.field_updates.append(sent)
            if self.reject_ids and any("id" in option for option in sent):
                return {"errors": [{"message": "id is not allowed"}]}
            self.options = [
                {"id": f"new-{i}", **{k: v for k, v in o.items() if k != "id"}}
                for i, o in enumerate(sent)
            ]
            return {"data": {}}

        if "repository(owner" in query:
            return {"data": {"repository": {"id": "repo-1"}}}

        if "convertProjectV2DraftIssueItemToIssue" in query:
            self.converted.append(variables["item"])
            return {
                "data": {
                    "convertProjectV2DraftIssueItemToIssue": {
                        "item": {
                            "id": variables["item"],
                            "content": {
                                "number": 41,
                                "title": "Draft idea",
                                "body": "do it",
                                "state": "OPEN",
                                "repository": {"nameWithOwner": "me/repo"},
                            },
                        }
                    }
                }
            }

        return {
            "data": {
                "user": {
                    "projectV2": {
                        "id": "proj-1",
                        "title": "Fresh",
                        "field": {"id": "field-1", "options": self.options},
                        "items": {"nodes": self.nodes},
                    }
                }
            }
        }


def test_a_new_boards_missing_columns_are_added_and_existing_ones_kept() -> None:
    board = GrowingBoard(DEFAULTS)
    api = make_api(board)
    api.load()

    api.ensure_columns()

    sent = board.field_updates[0]
    assert [option["name"] for option in sent[:3]] == ["Todo", "In Progress", "Done"]
    assert [option["id"] for option in sent[:3]] == ["d1", "d2", "d3"]
    assert sent[2]["description"] == "finished"
    assert api.missing_columns() == []


def test_columns_are_added_without_ids_when_github_rejects_them() -> None:
    board = GrowingBoard(DEFAULTS, reject_ids=True)
    api = make_api(board)
    api.load()

    api.ensure_columns()

    assert len(board.field_updates) == 2
    assert api.missing_columns() == []


def test_a_fresh_board_is_set_up_and_then_worked_from() -> None:
    board = GrowingBoard(DEFAULTS, nodes=[card("a", 3, "Todo")])

    issues = ProjectIssueSource(make_api(board), "me/repo").list_issues()

    assert [issue.number for issue in issues] == [3]
    assert board.field_updates


def draft(item_id, column):
    return {
        "id": item_id,
        "fieldValueByName": {"name": column},
        "content": {"__typename": "DraftIssue", "title": "Draft idea", "body": "do it"},
    }


def test_a_draft_task_in_todo_becomes_an_issue() -> None:
    board = GrowingBoard(OPTIONS, nodes=[draft("dr1", "Todo"), draft("dr2", "Done")])

    issues = ProjectIssueSource(make_api(board), "me/repo").list_issues()

    assert [(i.number, i.title, i.project_item_id) for i in issues] == [
        (41, "Draft idea", "dr1")
    ]
    assert board.converted == ["dr1"]
