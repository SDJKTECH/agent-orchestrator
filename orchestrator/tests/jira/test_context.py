import inspect
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.app.core.exceptions import IssueSourceError
from orchestrator.app.db.database import Base
from orchestrator.app.db.models import Milestone, Project, Task, TaskStatus
from orchestrator.app.jira import context, merged, messages, tracker
from orchestrator.app.jira.columns import PR_OPEN
from orchestrator.app.jira.context import describe_hierarchy, summarize_hierarchy
from orchestrator.app.jira.models import JiraItem
from orchestrator.app.pipeline.pipeline_service import PipelineService
from orchestrator.app.planning.planner import build_prompt


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        db.add(Project(name="Snake Game"))
        db.add(Milestone(project_id=1, code="M1", title="M1"))
        db.add(
            JiraItem(
                key="SG-1",
                project_id=1,
                type="Epic",
                summary="Snake game",
                description="A playable snake game",
            )
        )
        db.flush()
        db.add(
            JiraItem(
                key="SG-2",
                project_id=1,
                type="Story",
                summary="Obstacles",
                description="Walls the snake must avoid",
                parent_key="SG-1",
            )
        )
        db.flush()
        db.add(
            JiraItem(
                key="SG-3",
                project_id=1,
                type="Task",
                summary="Draw walls",
                parent_key="SG-2",
            )
        )
        db.commit()
        yield db


def jira_task(session, key="SG-3", with_item=True) -> Task:
    item = session.query(JiraItem).filter_by(key=key).first() if with_item else None
    task = Task(
        milestone_id=1,
        title=f"{key}: Draw walls",
        description="Draw the walls.",
        jira_key=key,
        jira_item_id=item.id if item else None,
    )
    session.add(task)
    session.commit()
    return task


def test_the_story_and_epic_are_described_outermost_first(session) -> None:
    text = describe_hierarchy(jira_task(session))

    assert text.index('Epic SG-1 "Snake game"') < text.index('Story SG-2 "Obstacles"')
    assert "A playable snake game" in text
    assert "Walls the snake must avoid" in text
    assert "implement just this task" in text


def test_a_long_parent_description_is_cut(session) -> None:
    story = session.query(JiraItem).filter_by(key="SG-2").one()
    story.description = "x" * 5000
    session.commit()

    text = describe_hierarchy(jira_task(session))

    assert text.count("x") == context.MAX_PARENT_DESCRIPTION_CHARS


def test_a_parent_without_text_says_so(session) -> None:
    epic = session.query(JiraItem).filter_by(key="SG-1").one()
    epic.description = ""
    session.commit()

    assert "(no description)" in describe_hierarchy(jira_task(session))


def test_a_task_without_parents_or_a_record_gets_no_context(session) -> None:
    epic_task = jira_task(session, key="SG-1")
    unlinked = jira_task(session, with_item=False)

    for task in (epic_task, unlinked):
        assert describe_hierarchy(task) == ""
        assert summarize_hierarchy(task) == ""


def test_the_planner_gets_a_one_line_summary(session) -> None:
    assert summarize_hierarchy(jira_task(session)) == (
        'Epic SG-1 "Snake game" > Story SG-2 "Obstacles"'
    )


@pytest.mark.parametrize("role", ["builder", "tester", "reviewer"])
def test_every_agent_prompt_carries_the_parents(session, role) -> None:
    task = jira_task(session)

    prompt = PipelineService._build_prompt(task, role)

    assert 'Story SG-2 "Obstacles"' in prompt
    assert "Walls the snake must avoid" in prompt
    assert prompt.index("Draw the walls.") < prompt.index("Walls the snake")


def test_a_fixing_builder_also_sees_the_parents(session) -> None:
    prompt = PipelineService._build_prompt(jira_task(session), "builder", "tests fail")

    assert 'Epic SG-1 "Snake game"' in prompt
    assert "tests fail" in prompt


def test_a_prompt_without_parents_is_unchanged(session) -> None:
    prompt = PipelineService._build_prompt(
        jira_task(session, with_item=False), "tester"
    )

    assert "larger piece of work" not in prompt
    assert prompt.startswith("You are the tester.")


def test_the_planner_is_told_which_story_each_task_belongs_to(session) -> None:
    prompt = build_prompt([jira_task(session), jira_task(session, with_item=False)])

    assert 'Belongs to: Epic SG-1 "Snake game" > Story SG-2 "Obstacles"' in prompt
    assert prompt.count("Belongs to:") == 1
    assert "not a dependency" in prompt


def make_pending(session, url="https://github.com/o/r/pull/3") -> Task:
    task = Task(
        milestone_id=1,
        title="SG-3: t",
        jira_key="SG-3",
        board_column=PR_OPEN,
        pull_request_url=url,
    )
    session.add(task)
    session.commit()
    return task


def test_an_unmerged_pull_request_does_not_finish_the_task(session) -> None:
    task = make_pending(session)
    pulls = MagicMock()
    pulls.request.return_value = {"merged": False}
    move = MagicMock()

    assert merged.sync_merged_tasks(session, pulls, move) == []
    move.assert_not_called()
    assert task.board_column == PR_OPEN


def test_a_pull_request_that_cannot_be_read_is_skipped(session) -> None:
    make_pending(session)
    pulls = MagicMock()
    pulls.request.side_effect = IssueSourceError("boom")

    assert merged.sync_merged_tasks(session, pulls, MagicMock()) == []


def test_a_task_without_a_pull_number_or_without_github_is_skipped(session) -> None:
    make_pending(session, url="https://example.com/not-a-pr")
    pulls = MagicMock()

    assert merged.sync_merged_tasks(session, pulls, MagicMock()) == []
    pulls.request.assert_not_called()
    assert merged.sync_merged_tasks(session, None, MagicMock()) == []


def test_the_outcome_comment_lists_the_pull_request(session) -> None:
    task = make_pending(session)
    task.status = TaskStatus.COMPLETED

    assert "Pull request: https://github.com/o/r/pull/3" in messages.outcome_message(
        task
    )


def test_jira_modules_use_github_only_for_the_pull_request_client() -> None:
    for module in (context, merged, messages):
        assert "app.github" not in inspect.getsource(module)

    github_lines = [
        line for line in inspect.getsource(tracker).splitlines() if "app.github" in line
    ]
    assert github_lines == [
        "from orchestrator.app.github.client import GitHubApi, detect_github_repo"
    ]
