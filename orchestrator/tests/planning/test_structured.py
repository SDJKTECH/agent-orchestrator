# ruff: noqa: F811  (client and session are fixtures shared from test_requirements)
import json

import pytest
from sqlalchemy import select

from orchestrator.app.api import requirement_routes
from orchestrator.app.core.exceptions import PlanningError
from orchestrator.app.jira.client import JiraApi
from orchestrator.app.jira.creator import create_issues
from orchestrator.app.jira.models import Requirement
from orchestrator.app.planning.requirement_service import plan_requirement
from orchestrator.app.planning.requirements import (
    RequirementPlanner,
    build_prompt,
    parse_plan,
)
from orchestrator.tests.planning.test_requirements import (
    FakeJira,
    FakeRunner,
    StubPlanner,
    client,  # noqa: F401
    plan_of,
    session,  # noqa: F401
    settings,
)

TWO_EPICS = {
    "assumptions": ["Order as written"],
    "epics": [
        {
            "summary": "Engine",
            "description": "The core",
            "stories": [
                {
                    "summary": "Board",
                    "subtasks": [
                        {"ref": "A", "summary": "Grid"},
                        {"ref": "B", "summary": "Snake", "depends_on": ["A"]},
                    ],
                }
            ],
        },
        {
            "summary": "Polish",
            "stories": [
                {
                    "summary": "Look",
                    "subtasks": [
                        {"ref": "C", "summary": "Colours", "depends_on": ["B"]}
                    ],
                }
            ],
        },
    ],
}


def structured_plan(reply=TWO_EPICS):
    return parse_plan(json.dumps(reply), "Snake", 8, 8, structured=True)


def test_a_structured_plan_can_have_several_epics() -> None:
    plan = structured_plan()

    assert [epic.summary for epic in plan.epics] == ["Engine", "Polish"]
    assert [story.summary for story in plan.stories] == ["Board", "Look"]
    # A Subtask may wait for one under another Epic.
    assert [sub.depends_on for sub in plan.subtasks()] == [[], ["A"], ["B"]]


def test_assumptions_are_added_to_every_epic() -> None:
    plan = structured_plan()

    assert all("Order as written" in epic.description for epic in plan.epics)


def test_without_structured_mode_only_the_first_epic_is_kept() -> None:
    plan = parse_plan(json.dumps(TWO_EPICS), "Snake", 8, 8)

    assert [epic.summary for epic in plan.epics] == ["Engine"]
    assert [sub.ref for sub in plan.subtasks()] == ["A", "B"]


def test_a_structured_plan_is_not_held_to_the_size_limits() -> None:
    reply = {
        "epics": [
            {
                "summary": "Big",
                "stories": [
                    {
                        "summary": f"Story {n}",
                        "subtasks": [
                            {"ref": f"R{n}-{m}", "summary": f"Sub {n}.{m}"}
                            for m in range(12)
                        ],
                    }
                    for n in range(12)
                ],
            }
        ]
    }

    structured = parse_plan(json.dumps(reply), "T", 2, 2, structured=True)
    normal = parse_plan(json.dumps(reply), "T", 2, 2)

    assert len(structured.stories) == 12 and len(structured.subtasks()) == 144
    assert len(normal.stories) == 2 and len(normal.subtasks()) == 4


def test_titles_are_kept_word_for_word() -> None:
    reply = {
        "epics": [
            {
                "summary": "  Épic — with ünicode & symbols!  ",
                "stories": [
                    {
                        "summary": "As a user, I can log in",
                        "subtasks": [{"ref": "x", "summary": "Add /login (POST)"}],
                    }
                ],
            }
        ]
    }

    plan = structured_plan(reply)

    assert plan.epics[0].summary == "Épic — with ünicode & symbols!"
    assert plan.stories[0].summary == "As a user, I can log in"
    assert plan.subtasks()[0].summary == "Add /login (POST)"


def test_an_epic_with_no_usable_story_or_title_is_left_out() -> None:
    one = {"summary": "S", "subtasks": [{"ref": "a", "summary": "t"}]}
    two = {"summary": "S", "subtasks": [{"ref": "b", "summary": "t"}]}
    reply = {
        "epics": [
            {"summary": "Empty", "stories": []},
            {"stories": [one]},
            {"summary": "Real", "stories": [two]},
        ]
    }

    plan = structured_plan(reply)

    assert [epic.summary for epic in plan.epics] == ["Real"]


def test_the_structured_prompt_asks_for_exactly_what_was_written() -> None:
    structured = build_prompt("T", "Epic: X\n- Story: Y", 8, 8, structured=True)
    normal = build_prompt("T", "Epic: X", 8, 8)

    assert structured.startswith("STRUCTURED MODE")
    assert "word for word" in structured and "Epic: X\n- Story: Y" in structured
    assert "STRUCTURED MODE" not in normal


def test_the_planner_agent_is_told_when_the_structure_is_given(tmp_path) -> None:
    runner = FakeRunner(json.dumps(TWO_EPICS))
    planner = RequirementPlanner(settings(repository_path=str(tmp_path)), runner)

    plan = planner.plan("Snake", "Epic: Engine ...", structured=True)

    assert len(plan.epics) == 2
    assert runner.commands[0][-1].startswith("STRUCTURED MODE")

    planner.plan("Snake", "text")
    assert not runner.commands[1][-1].startswith("STRUCTURED MODE")


def test_every_epic_story_and_subtask_is_made_with_its_parent() -> None:
    transport = FakeJira()

    created = create_issues(
        JiraApi("https://x.test", "e", "t", transport), settings(), structured_plan()
    )

    fields = transport.created()
    types = [f["issuetype"]["name"] for f in fields]
    parents = [f.get("parent", {}).get("key") for f in fields]
    assert types == ["Epic", "Story", "Subtask", "Subtask", "Epic", "Story", "Subtask"]
    assert parents == [None, "SG-1", "SG-2", "SG-2", None, "SG-5", "SG-6"]
    assert created.epics == ["SG-1", "SG-5"]
    assert created.stories == ["SG-2", "SG-6"]
    assert created.epic == "SG-1"
    # Colours (SG-7) waits for Snake (SG-4), which is under the other Epic.
    links = [
        (link["inwardIssue"]["key"], link["outwardIssue"]["key"])
        for link in transport.links()
    ]
    assert links == [("SG-3", "SG-4"), ("SG-4", "SG-7")]


def test_a_failure_under_the_second_epic_undoes_the_first_too() -> None:
    transport = FakeJira(fail_on=6)

    with pytest.raises(PlanningError, match="nothing was kept"):
        create_issues(
            JiraApi("https://x.test", "e", "t", transport),
            settings(),
            structured_plan(),
        )

    assert sorted(transport.deleted()) == sorted(
        f"/issue/SG-{n}?deleteSubtasks=true" for n in range(1, 6)
    )


class RecordingPlanner(StubPlanner):
    def __init__(self):
        super().__init__()
        self.seen = []

    def plan(self, title, text, structured=False):
        self.calls += 1
        self.seen.append(structured)
        return structured_plan() if structured else plan_of()


def test_the_service_passes_structured_on_and_keeps_every_epic(session) -> None:
    transport = FakeJira()
    api = JiraApi("https://x.test", "e", "t", transport)
    planner = RecordingPlanner()

    result = plan_requirement(
        session, planner, api, settings(), "Snake", "t", "github#2", structured=True
    )

    assert planner.seen == [True]
    assert result.epic_key == "SG-1" and result.epic_keys == ["SG-1", "SG-5"]
    assert session.scalar(select(Requirement)).epic_key == "SG-1,SG-5"
    comments = [p for m, p, _ in transport.calls if p.endswith("/comment")]
    assert comments == ["/issue/SG-1/comment", "/issue/SG-5/comment"]


def test_a_structured_requirement_planned_before_returns_all_its_epics(
    session,
) -> None:
    api = JiraApi("https://x.test", "e", "t", FakeJira())
    planner = RecordingPlanner()
    args = (session, planner, api, settings(), "S", "t", "g#2")
    plan_requirement(*args, structured=True)

    again = plan_requirement(*args, structured=True)

    assert again.existing and again.epic_keys == ["SG-1", "SG-5"]
    assert planner.calls == 1


def test_the_endpoint_creates_exactly_the_structure_when_asked(client) -> None:
    http, transport = client
    http.app.dependency_overrides[requirement_routes.get_planner] = RecordingPlanner

    body = http.post(
        "/requirements", json={"title": "Snake", "description": "d", "structured": True}
    ).json()

    assert body["epic_keys"] == ["SG-1", "SG-5"]
    assert [epic["summary"] for epic in body["epics"]] == ["Engine", "Polish"]
    assert [epic["key"] for epic in body["epics"]] == ["SG-1", "SG-5"]
    assert [story["key"] for story in body["epics"][1]["stories"]] == ["SG-6"]
    assert len(body["stories"]) == 2


def test_the_endpoint_is_not_structured_unless_asked(client) -> None:
    http, _ = client
    planner = RecordingPlanner()
    http.app.dependency_overrides[requirement_routes.get_planner] = lambda: planner

    http.post("/requirements", json={"title": "Snake", "dry_run": True})

    assert planner.seen == [False]
