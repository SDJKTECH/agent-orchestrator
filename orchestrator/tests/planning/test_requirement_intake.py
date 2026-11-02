from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from orchestrator.app.core.exceptions import IssueSourceError, PlanningError
from orchestrator.app.db.database import Base
from orchestrator.app.github.client import GitHubApi
from orchestrator.app.github.intake import Issue
from orchestrator.app.jira.client import JiraApi
from orchestrator.app.jira.models import Requirement
from orchestrator.app.planning import requirement_intake
from orchestrator.app.planning.requirement_intake import (
    MAX_ATTEMPTS,
    MAX_PER_CYCLE,
    GitHubRequirementSource,
    RequirementIntake,
    make_requirement_intake,
    requirement_text,
)
from orchestrator.tests.planning.test_requirements import (
    FakeJira,
    StubPlanner,
    settings,
)


class FakeSource:
    def __init__(self, issues):
        self.issues = issues
        self.talk = {}
        self.comments = []
        self.marked = []

    def list_requirements(self):
        return self.issues

    def discussion(self, number):
        return self.talk.get(number, [])

    def comment(self, number, body):
        self.comments.append((number, body))
        return True

    def mark_planned(self, number):
        self.marked.append(number)
        return True


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'i.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def make_intake(factory, issues, planner=None):
    source = FakeSource(issues)
    transport = FakeJira()
    intake = RequirementIntake(
        settings(poll_interval_seconds=1),
        factory,
        source,
        planner or StubPlanner(),
        JiraApi("https://x.test", "e", "t", transport),
    )
    return intake, source, transport


def issue(number, title="Snake game"):
    return Issue(number=number, title=title, body="Build it")


def test_a_new_requirement_is_planned_and_reported_on_the_issue(factory) -> None:
    intake, source, transport = make_intake(factory, [issue(7)])

    assert intake.run_once() == ["SG-1"]

    [(number, body)] = source.comments
    assert number == 7
    assert "Epic SG-1" in body and "https://x.test/browse/SG-1" in body
    assert "- SG-2 Board" in body
    assert "  - SG-4 Snake (after SG-3)" in body
    assert source.marked == [7]
    with factory() as session:
        assert session.scalar(select(Requirement)).reference == "github#7"


def test_a_requirement_is_planned_only_once(factory) -> None:
    planner = StubPlanner()
    intake, source, transport = make_intake(factory, [issue(7)], planner)

    intake.run_once()
    assert intake.run_once() == []

    assert planner.calls == 1
    assert len(source.comments) == 1


def test_a_requirement_planned_before_a_restart_is_not_planned_again(factory) -> None:
    planner = StubPlanner()
    make_intake(factory, [issue(7)], planner)[0].run_once()

    fresh, source, _ = make_intake(factory, [issue(7)], planner)

    assert fresh.run_once() == []
    assert planner.calls == 1
    assert source.comments == []


def test_only_a_few_requirements_are_planned_per_cycle(factory) -> None:
    issues = [issue(n) for n in range(1, MAX_PER_CYCLE + 3)]
    intake, _, _ = make_intake(factory, issues)

    assert len(intake.run_once()) == MAX_PER_CYCLE
    assert len(intake.run_once()) == 2


class BrokenPlanner:
    def __init__(self):
        self.calls = 0

    def plan(self, title, text, structured=False):
        self.calls += 1
        raise PlanningError("no plan")


def test_a_requirement_that_cannot_be_planned_is_given_up_after_a_few_tries(
    factory,
) -> None:
    planner = BrokenPlanner()
    intake, source, transport = make_intake(factory, [issue(7)], planner)

    for _ in range(MAX_ATTEMPTS + 2):
        assert intake.run_once() == []

    assert planner.calls == MAX_ATTEMPTS
    # Told once, on the last try; nothing was made in Jira.
    [(number, body)] = source.comments
    assert number == 7 and "could not plan" in body and "no plan" in body
    assert source.marked == []
    assert transport.calls == []
    with factory() as session:
        assert session.scalar(select(Requirement)) is None


def test_one_bad_requirement_does_not_stop_the_others(factory) -> None:
    class PickyPlanner(StubPlanner):
        def plan(self, title, text, structured=False):
            if title == "Bad":
                raise PlanningError("no plan")
            return super().plan(title, text, structured)

    intake, source, _ = make_intake(
        factory, [issue(1, "Bad"), issue(2, "Good")], PickyPlanner()
    )

    assert intake.run_once() == ["SG-1"]
    assert [n for n, _ in source.comments] == [2]


class FakeGitHub(GitHubApi):
    def __init__(self, items, token="t"):
        super().__init__("o/r", token)
        self.items = items
        self.sent = []

    def request(self, method, path, body=None):
        self.sent.append((method, path, body))
        return self.items if method == "GET" else {}


def test_the_github_source_reads_labelled_issues_and_skips_pull_requests() -> None:
    api = FakeGitHub(
        [
            {
                "number": 1,
                "title": " Snake ",
                "body": None,
                "labels": [{"name": "requirement"}],
            },
            {"number": 2, "title": "A PR", "pull_request": {}},
            {"number": 3, "title": "  "},
            "junk",
        ]
    )

    issues = GitHubRequirementSource(api, "requirement").list_requirements()

    assert [(i.number, i.title, i.body, i.labels) for i in issues] == [
        (1, "Snake", "", ["requirement"])
    ]
    assert "labels=requirement" in api.sent[0][1]


def test_the_github_source_rejects_a_reply_that_is_not_a_list() -> None:
    with pytest.raises(IssueSourceError):
        GitHubRequirementSource(FakeGitHub({"message": "x"}), "r").list_requirements()


def test_the_github_source_comments_and_labels_when_it_can_write() -> None:
    api = FakeGitHub([])
    source = GitHubRequirementSource(api, "requirement", "planned")

    assert source.comment(5, "hi") and source.mark_planned(5)

    assert api.sent == [
        ("POST", "/issues/5/comments", {"body": "hi"}),
        ("POST", "/issues/5/labels", {"labels": ["planned"]}),
    ]


def test_the_github_source_writes_nothing_without_a_token_or_label() -> None:
    api = FakeGitHub([], token=None)

    assert not GitHubRequirementSource(api, "r", "planned").comment(5, "hi")
    assert not GitHubRequirementSource(FakeGitHub([]), "r", "").mark_planned(5)
    assert api.sent == []


def test_the_intake_is_off_without_a_github_repo_or_jira(monkeypatch) -> None:
    monkeypatch.setattr(requirement_intake, "detect_github_repo", lambda path: None)
    config = SimpleNamespace(github_repo=None, repository_path=None)

    assert make_requirement_intake(config, None) is None

    def no_jira(config):
        raise IssueSourceError("missing")

    monkeypatch.setattr(requirement_intake, "make_jira_api", no_jira)
    config = SimpleNamespace(github_repo="o/r", repository_path=None)

    assert make_requirement_intake(config, None) is None


def test_the_intake_is_built_from_the_settings(monkeypatch, factory) -> None:
    monkeypatch.setattr(
        requirement_intake,
        "make_jira_api",
        lambda config: JiraApi("https://x.test", "e", "t", FakeJira()),
    )
    config = settings(
        github_repo="o/r",
        github_token=None,
        requirement_label="req",
        requirement_planned_label="done",
        poll_interval_seconds=5,
    )

    intake = make_requirement_intake(config, factory)

    assert intake is not None
    assert intake.source.label == "req" and intake.source.planned_label == "done"
    assert intake.interval_seconds == 5


def test_the_text_planned_includes_labels_and_the_discussion() -> None:
    issue = Issue(
        number=1, title="T", body=" Make a game ", labels=["requirement", "p1"]
    )

    text = requirement_text(issue, ["Use Python", "Keep it small"])

    assert text.startswith("Make a game")
    assert "Labels: requirement, p1" in text
    assert "Use Python\n\n---\n\nKeep it small" in text


def test_an_issue_with_only_a_title_gives_empty_text() -> None:
    assert requirement_text(Issue(number=1, title="T"), []) == ""


def test_the_planner_is_given_the_comments_of_the_issue(factory) -> None:
    seen = []

    class Spy(StubPlanner):
        def plan(self, title, text, structured=False):
            seen.append((title, text))
            return super().plan(title, text, structured)

    intake, source, _ = make_intake(factory, [issue(7)], Spy())
    source.talk[7] = ["Please use Python"]

    intake.run_once()

    [(title, text)] = seen
    assert title == "Snake game"
    assert "Build it" in text and "Please use Python" in text


def test_the_assumptions_are_put_to_the_author_on_the_issue(factory) -> None:
    class Guessing(StubPlanner):
        def plan(self, title, text, structured=False):
            plan = super().plan(title, text, structured)
            plan.assumptions = ["It is a terminal game"]
            return plan

    intake, source, _ = make_intake(factory, [issue(7)], Guessing())

    intake.run_once()

    [(_, body)] = source.comments
    assert "I had to assume the following" in body
    assert "- It is a terminal game" in body


def test_no_assumptions_section_when_there_were_none(factory) -> None:
    intake, source, _ = make_intake(factory, [issue(7)])

    intake.run_once()

    assert "assume" not in source.comments[0][1]


def test_the_github_source_reads_the_comments_of_an_issue() -> None:
    api = FakeGitHub(
        [{"body": " first "}, {"body": ""}, {"nobody": 1}, "junk", {"body": "second"}]
    )

    assert GitHubRequirementSource(api, "r").discussion(5) == ["first", "second"]
    assert api.sent[0][1].startswith("/issues/5/comments")


def test_unreadable_comments_are_left_out_not_fatal() -> None:
    class Broken(FakeGitHub):
        def request(self, method, path, body=None):
            raise IssueSourceError("down")

    assert GitHubRequirementSource(Broken([]), "r").discussion(5) == []
    assert GitHubRequirementSource(FakeGitHub({"x": 1}), "r").discussion(5) == []
