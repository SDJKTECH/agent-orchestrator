# ruff: noqa: F811  (factory is a fixture shared from test_requirement_intake)
from orchestrator.app.github.intake import Issue
from orchestrator.tests.planning.test_requirement_intake import (
    factory,  # noqa: F401
    make_intake,
)
from orchestrator.tests.planning.test_structured import RecordingPlanner


def labelled(number, *labels):
    return Issue(number=number, title="Snake game", body="Epic: A", labels=list(labels))


def test_the_structured_label_makes_the_planner_follow_the_issue(factory) -> None:
    planner = RecordingPlanner()
    intake, _, _ = make_intake(
        factory, [labelled(1, "requirement", "structured")], planner
    )

    intake.run_once()

    assert planner.seen == [True]


def test_the_label_is_matched_without_regard_to_case(factory) -> None:
    planner = RecordingPlanner()
    intake, _, _ = make_intake(
        factory, [labelled(1, "requirement", "Structured")], planner
    )

    intake.run_once()

    assert planner.seen == [True]


def test_an_issue_without_the_label_is_planned_freely(factory) -> None:
    planner = RecordingPlanner()
    intake, _, _ = make_intake(factory, [labelled(1, "requirement")], planner)

    intake.run_once()

    assert planner.seen == [False]


def test_an_empty_label_setting_turns_the_mode_off(factory) -> None:
    planner = RecordingPlanner()
    intake, _, _ = make_intake(factory, [labelled(1, "structured")], planner)
    intake.settings.requirement_structured_label = ""

    intake.run_once()

    assert planner.seen == [False]


def test_the_comment_lists_every_epic_with_its_stories_and_subtasks(factory) -> None:
    intake, source, _ = make_intake(
        factory, [labelled(1, "structured")], RecordingPlanner()
    )

    assert intake.run_once() == ["SG-1"]

    [(_, body)] = source.comments
    assert body.startswith("Planned in Jira as Epics SG-1, SG-5")
    assert "Epic SG-1 Engine: https://x.test/browse/SG-1" in body
    assert "Epic SG-5 Polish: https://x.test/browse/SG-5" in body
    assert "- SG-2 Board" in body and "- SG-6 Look" in body
    assert "  - SG-4 Snake (after SG-3)" in body
    assert "  - SG-7 Colours (after SG-4)" in body
    assert "Order as written" in body
    assert body == body.rstrip()
