import inspect
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from orchestrator.app.agents.execution_manager import ExecutionManager
from orchestrator.app.core.config import Settings
from orchestrator.app.db.database import Base
from orchestrator.app.db.models import Task, TaskStatus
from orchestrator.app.github import projects
from orchestrator.app.jira import columns, poller
from orchestrator.app.jira.poller import JiraPoller
from orchestrator.app.planning.planner import Dependency
from orchestrator.tests.helpers import init_repository
from orchestrator.tests.jira.test_importer import EPIC, STORY, StaticItemSource, work


@pytest.fixture
def env(tmp_path):
    repository = tmp_path / "source"
    init_repository(repository)
    engine = create_engine(f"sqlite:///{(tmp_path / 'poll.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    manager = ExecutionManager(max_workers=3)
    yield repository, factory, manager
    manager.shutdown(wait=True)
    engine.dispose()


def make_poller(env, items, planner=None, tracker=None):
    repository, factory, manager = env
    tracker = tracker or MagicMock()
    return (
        JiraPoller(
            settings=Settings(
                _env_file=None,
                repository_path=repository,
                poll_interval_seconds=1,
                issue_source="jira",
                plan_dependencies=False,
            ),
            session_factory=factory,
            manager=manager,
            run_pipeline=lambda task_id: None,
            tracker=tracker,
            source=StaticItemSource(items),
            planner=planner,
        ),
        tracker,
        factory,
    )


def test_a_task_is_imported_once_with_its_story_and_epic(env) -> None:
    jira_poller, tracker, factory = make_poller(
        env, [work("SG-3", "SG-2", [EPIC, STORY])]
    )

    first = jira_poller.poll_once()
    jira_poller.poll_once()

    with factory() as session:
        [task] = session.scalars(select(Task)).all()
        assert [a.key for a in task.jira_item.ancestors()] == ["SG-1", "SG-2"]
        assert task.github_issue_number is None

    assert first == [task.id]
    # Imported (and Jira told) once; the no-op pipeline leaves it queued.
    tracker.move.assert_called_once()
    assert tracker.move.call_args.args == (
        tracker.move.call_args.args[0],
        columns.QUEUED,
    )
    tracker.sync_merged.assert_called()


def test_a_dependent_task_is_held_and_the_rest_run(env) -> None:
    planner = MagicMock()
    planner.plan.return_value = {"T-3": [Dependency("T-2", "uses it")]}
    jira_poller, tracker, factory = make_poller(
        env, [work(f"T-{n}", project="P") for n in (1, 2, 3)], planner=planner
    )

    scheduled = jira_poller.poll_once()

    with factory() as session:
        tasks = {t.jira_key: t for t in session.scalars(select(Task))}

    assert tasks["T-3"].status == TaskStatus.BLOCKED
    assert sorted(scheduled) == sorted([tasks["T-1"].id, tasks["T-2"].id])
    moves = {c.args[0].jira_key: c.args[1] for c in tracker.move.call_args_list}
    assert moves == {
        "T-1": columns.QUEUED,
        "T-2": columns.QUEUED,
        "T-3": columns.BLOCKED,
    }
    tracker.link_blocks.assert_called_once()
    assert tracker.link_blocks.call_args.args[0].jira_key == "T-3"


def test_a_link_made_in_jira_blocks_without_writing_a_new_link(env) -> None:
    jira_poller, tracker, factory = make_poller(
        env, [work("T-1"), work("T-2", blocked_by=["T-1"])]
    )

    jira_poller.poll_once()

    with factory() as session:
        tasks = {t.jira_key: t for t in session.scalars(select(Task))}

    assert tasks["T-2"].status == TaskStatus.BLOCKED
    assert tasks["T-1"].status != TaskStatus.BLOCKED
    tracker.link_blocks.assert_not_called()


def test_a_planner_failure_lets_the_tasks_run_independently(env) -> None:
    planner = MagicMock()
    planner.plan.side_effect = RuntimeError("planner down")
    jira_poller, _, factory = make_poller(
        env, [work("T-1"), work("T-2")], planner=planner
    )

    scheduled = jira_poller.poll_once()

    assert len(scheduled) == 2


def test_a_source_failure_is_raised_for_the_loop_to_log(env) -> None:
    jira_poller, _, _ = make_poller(env, [])
    jira_poller.source = MagicMock()
    jira_poller.source.list_items.side_effect = RuntimeError("Jira down")

    with pytest.raises(RuntimeError):
        jira_poller.poll_once()


def test_the_poller_needs_a_repository(tmp_path) -> None:
    with pytest.raises(ValueError, match="repository_path"):
        JiraPoller(
            settings=Settings(_env_file=None),
            session_factory=MagicMock(),
            manager=MagicMock(),
            run_pipeline=lambda task_id: None,
            tracker=MagicMock(),
        )


def test_the_jira_columns_match_the_ones_the_pipeline_sends() -> None:
    names = ("QUEUED", "BLOCKED", "BUILDING", "TESTING", "CHECKS")
    names += ("REVIEWING", "FIXING", "PR_OPEN", "DONE", "FAILED")

    for name in names:
        assert getattr(columns, name) == getattr(projects, name)


def test_the_jira_poller_does_not_use_the_github_package() -> None:
    assert "app.github" not in inspect.getsource(poller)
    assert "app.github" not in inspect.getsource(columns)
