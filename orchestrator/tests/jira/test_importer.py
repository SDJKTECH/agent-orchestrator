import inspect

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from orchestrator.app.db.database import Base
from orchestrator.app.db.models import Milestone, Project, Task
from orchestrator.app.jira import importer
from orchestrator.app.jira.importer import sync_jira_items
from orchestrator.app.jira.models import JiraItem
from orchestrator.app.jira.source import JiraRecord, JiraWorkItem
from orchestrator.tests.helpers import init_repository


class StaticItemSource:
    def __init__(self, items: list[JiraWorkItem]) -> None:
        self.items = items

    def list_items(self) -> list[JiraWorkItem]:
        return self.items


def work(
    key,
    parent=None,
    ancestors=(),
    project="Snake Game",
    version=None,
    blocked_by=(),
    summary=None,
) -> JiraWorkItem:
    return JiraWorkItem(
        record=JiraRecord(
            key=key,
            type="Task",
            summary=summary or f"{key} summary",
            description=f"{key} text",
            status="To Do",
            parent_key=parent,
        ),
        ancestors=list(ancestors),
        url=f"https://x.atlassian.net/browse/{key}",
        fix_version=version,
        project_name=project,
        blocked_by=list(blocked_by),
    )


EPIC = JiraRecord("SG-1", "Epic", "Snake game", "The whole game")
STORY = JiraRecord("SG-2", "Story", "Obstacles", "Walls on the board", None, "SG-1")


@pytest.fixture
def env(tmp_path):
    repository = tmp_path / "source"
    init_repository(repository)
    engine = create_engine(f"sqlite:///{(tmp_path / 'jira.db').as_posix()}")
    Base.metadata.create_all(bind=engine)

    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        project = Project(name="Main", local_path=str(repository))
        milestone = Milestone(project=project, code="GH", title="Default")
        session.add(milestone)
        session.commit()
        yield session, milestone.id, tmp_path / "agent-worktrees"

    engine.dispose()


def run(env, *items):
    session, milestone_id, worktrees = env
    return sync_jira_items(
        session, milestone_id, StaticItemSource(list(items)), worktrees
    )


def test_a_task_is_saved_with_its_story_and_epic(env) -> None:
    session = env[0]

    result = run(env, work("SG-3", "SG-2", [EPIC, STORY]))

    [task] = result.imported
    assert task.jira_key == "SG-3"
    assert task.title == "SG-3: SG-3 summary"
    assert "SG-3 text" in task.description
    assert task.jira_item.key == "SG-3"
    assert [a.key for a in task.jira_item.ancestors()] == ["SG-1", "SG-2"]
    assert task.jira_item.ancestors()[1].description == "Walls on the board"
    assert (
        session.scalar(select(JiraItem).where(JiraItem.key == "SG-1")).parent_key
        is None
    )


def test_the_task_is_filed_under_its_jira_project_and_default_milestone(env) -> None:
    session = env[0]

    [task] = run(env, work("SG-3")).imported

    assert task.milestone.project.name == "Snake Game"
    assert task.milestone.project.local_path == session.get(Project, 1).local_path
    assert (task.milestone.code, task.milestone.title) == ("JIRA", "Jira issues")
    assert task.jira_item.project_id == task.milestone.project_id


def test_a_fix_version_picks_the_milestone(env) -> None:
    [task] = run(env, work("SG-3", version="M2 - Obstacles")).imported

    assert (task.milestone.code, task.milestone.title) == ("M2", "M2 - Obstacles")


def test_a_task_is_imported_once(env) -> None:
    session = env[0]
    first = run(env, work("SG-3", "SG-2", [EPIC, STORY]))
    second = run(env, work("SG-3", "SG-2", [EPIC, STORY]))

    assert len(first.imported) == 1
    assert second.imported == []
    assert second.skipped == ["SG-3"]
    assert len(session.scalars(select(Task)).all()) == 1


def test_tasks_of_one_story_share_its_row(env) -> None:
    session = env[0]

    result = run(
        env,
        work("SG-3", "SG-2", [EPIC, STORY]),
        work("SG-4", "SG-2", [EPIC, STORY]),
    )

    assert len(result.imported) == 2
    assert len(session.scalars(select(JiraItem)).all()) == 4
    story = session.scalar(select(JiraItem).where(JiraItem.key == "SG-2"))
    assert sorted(child.key for child in story.children) == ["SG-3", "SG-4"]


def test_a_story_edited_in_jira_is_refreshed_on_the_next_import(env) -> None:
    session = env[0]
    run(env, work("SG-3", "SG-2", [EPIC, STORY]))
    edited = JiraRecord("SG-2", "Story", "Walls", "New text", None, "SG-1")

    run(env, work("SG-4", "SG-2", [EPIC, edited]))

    story = session.scalar(select(JiraItem).where(JiraItem.key == "SG-2"))
    assert (story.summary, story.description) == ("Walls", "New text")


def test_a_task_without_a_parent_has_no_ancestors(env) -> None:
    [task] = run(env, work("SG-9")).imported

    assert task.jira_item.parent_key is None
    assert task.jira_item.ancestors() == []


def test_a_task_directly_under_an_epic(env) -> None:
    epic_child = JiraRecord("SG-1", "Epic", "Snake game", "", None, None)

    [task] = run(env, work("SG-3", "SG-1", [epic_child])).imported

    assert [a.key for a in task.jira_item.ancestors()] == ["SG-1"]


def test_a_parent_that_was_cut_off_is_left_unlinked(env) -> None:
    story = JiraRecord("SG-2", "Story", "Obstacles", "", None, "SG-77")

    [task] = run(env, work("SG-3", "SG-2", [story])).imported

    parent = task.jira_item.ancestors()[0]
    assert parent.key == "SG-2"
    assert parent.parent_key is None


def test_blockers_are_reported_by_task_id(env) -> None:
    result = run(env, work("SG-3", blocked_by=["SG-2"]), work("SG-4"))

    first = result.imported[0]
    assert result.links == {first.id: ["SG-2"]}


def test_a_failed_import_is_reported_and_leaves_no_task(tmp_path) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)

    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        project = Project(name="No repo")
        milestone = Milestone(project=project, code="GH", title="Default")
        session.add(milestone)
        session.commit()

        result = sync_jira_items(
            session,
            milestone.id,
            StaticItemSource([work("SG-3", project=None)]),
            tmp_path / "worktrees",
        )

        assert result.imported == []
        assert "SG-3" in result.failed
        assert session.scalars(select(Task)).all() == []


def test_the_importer_does_not_use_the_github_package() -> None:
    assert "app.github" not in inspect.getsource(importer)
