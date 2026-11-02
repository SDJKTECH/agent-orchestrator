import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from orchestrator.app.db.database import Base
from orchestrator.app.db.migrations import migrate_jira_item_columns
from orchestrator.app.db.models import Milestone, Project, Task, TaskStatus
from orchestrator.app.jira.importer import sync_jira_items
from orchestrator.app.jira.merged import sync_merged_stories
from orchestrator.app.jira.stories import (
    StoryDelivery,
    prepare_story_branch,
    story_branch_name,
    story_item,
)
from orchestrator.app.planning.dependencies import is_merged, release_ready
from orchestrator.app.vcs.git_manager import commit_worktree
from orchestrator.tests.helpers import init_repository
from orchestrator.tests.jira.test_importer import EPIC, STORY, StaticItemSource, work


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class FakePublisher:
    def __init__(self, url="https://github.com/o/r/pull/9"):
        self.url = url
        self.calls = []

    def open_pull_request(self, task, head, title=None, body=None):
        self.calls.append((task.id, head, title, body))
        return self.url


@pytest.fixture
def env(tmp_path):
    repository = tmp_path / "source"
    init_repository(repository)
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(repository), str(remote)],
        check=True,
        capture_output=True,
    )
    git(repository, "remote", "add", "origin", str(remote))
    git(repository, "fetch", "origin")
    engine = create_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    Base.metadata.create_all(bind=engine)

    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        project = Project(name="Main", local_path=str(repository))
        milestone = Milestone(project=project, code="GH", title="Default")
        session.add(milestone)
        session.commit()
        yield session, milestone.id, repository, tmp_path / "agent-worktrees"

    engine.dispose()


def story_tasks(env, *keys):
    session, milestone_id, _, root = env
    items = [work(key, "SG-2", [EPIC, STORY]) for key in keys]
    return sync_jira_items(
        session, milestone_id, StaticItemSource(items), root
    ).imported


def build(task, name, content="x = 1\n"):
    (Path(task.worktree_path) / name).write_text(content, encoding="utf-8")
    commit_worktree(Path(task.worktree_path), f"build {name}")


def test_a_task_under_a_story_knows_its_story(env) -> None:
    (task,) = story_tasks(env, "SG-3")

    assert story_item(task).key == "SG-2"
    assert story_branch_name(story_item(task)) == "agent/story-sg-2"


def test_a_task_without_a_story_has_none(env) -> None:
    session, milestone_id, _, root = env
    items = [work("SG-3", None, [EPIC])]
    sync_jira_items(session, milestone_id, StaticItemSource(items), root)
    task = session.scalar(select(Task))

    assert story_item(task) is None
    assert prepare_story_branch(task, "main") is None


def test_the_story_pull_request_opens_only_when_every_task_is_in(env) -> None:
    session, _, repository, _ = env
    first, second = story_tasks(env, "SG-3", "SG-4")
    publisher = FakePublisher()
    delivery = StoryDelivery(session, publisher)
    build(first, "engine.py")
    build(second, "walls.py")
    item = story_item(first)

    assert delivery.deliver(first, item).ok
    assert publisher.calls == []
    assert item.pull_request_url is None

    assert delivery.deliver(second, item).ok

    assert len(publisher.calls) == 1
    _, head, title, body = publisher.calls[0]
    assert head == "agent/story-sg-2"
    assert title == "SG-2: Obstacles"
    assert "SG-3" in body and "SG-4" in body
    assert item.pull_request_url == publisher.url
    assert first.pull_request_url == second.pull_request_url == publisher.url
    files = git(repository, "ls-tree", "-r", "--name-only", "agent/story-sg-2")
    assert "engine.py" in files and "walls.py" in files


def test_a_failed_task_keeps_the_story_pull_request_back(env) -> None:
    session, *_ = env
    first, second = story_tasks(env, "SG-3", "SG-4")
    second.status = TaskStatus.FAILED
    publisher = FakePublisher()
    build(first, "engine.py")

    assert StoryDelivery(session, publisher).deliver(first, story_item(first)).ok
    assert publisher.calls == []


def test_a_cancelled_task_does_not_hold_the_story_back(env) -> None:
    session, *_ = env
    first, second = story_tasks(env, "SG-3", "SG-4")
    second.status = TaskStatus.CANCELLED
    publisher = FakePublisher()
    build(first, "engine.py")

    StoryDelivery(session, publisher).deliver(first, story_item(first))

    assert len(publisher.calls) == 1


def test_the_pull_request_is_opened_once(env) -> None:
    session, *_ = env
    (task,) = story_tasks(env, "SG-3")
    publisher = FakePublisher()
    delivery = StoryDelivery(session, publisher)
    build(task, "engine.py")

    delivery.deliver(task, story_item(task))
    delivery.deliver(task, story_item(task))

    assert len(publisher.calls) == 1


def test_a_task_that_conflicts_with_the_story_is_not_delivered(env) -> None:
    session, _, _, root = env
    first, second = story_tasks(env, "SG-3", "SG-4")
    build(first, "engine.py", "a = 1\n")
    build(second, "engine.py", "b = 2\n")
    delivery = StoryDelivery(session, FakePublisher())

    assert delivery.deliver(first, story_item(first)).ok
    result = delivery.deliver(second, story_item(second))

    assert not result.ok
    assert second.story_branch is None
    # The Story branch is left clean for the next try.
    assert git(root / "story-sg-2", "status", "--porcelain") == ""
    # The conflict is put in the task's own worktree, for the builder to resolve.
    assert "engine.py" in result.problem
    assert "<<<<<<<" in (Path(second.worktree_path) / "engine.py").read_text()


def test_a_task_in_the_story_branch_is_not_in_main_until_the_pull_request_merges(
    env,
) -> None:
    session, *_ = env
    (task,) = story_tasks(env, "SG-3")
    build(task, "engine.py")
    task.status = TaskStatus.COMPLETED
    StoryDelivery(session, FakePublisher()).deliver(task, story_item(task))

    assert not is_merged(task)

    story_item(task).pull_request_merged = True

    assert is_merged(task)


def test_a_dependent_task_is_cut_from_the_story_branch(env) -> None:
    session, _, repository, root = env
    blocker, waiting = story_tasks(env, "SG-3", "SG-4")
    waiting.blocked_by = json.dumps([{"task_id": blocker.id, "reason": "uses it"}])
    waiting.status = TaskStatus.BLOCKED
    session.commit()
    build(blocker, "engine.py")

    assert release_ready(session, repository, "origin", "main", jira_only=True) == []

    # The pipeline delivers the task, then marks it completed.
    StoryDelivery(session, FakePublisher()).deliver(blocker, story_item(blocker))
    blocker.status = TaskStatus.COMPLETED
    released = release_ready(session, repository, "origin", "main", jira_only=True)

    assert released == [waiting]
    assert (root / f"task-{waiting.id}" / "engine.py").is_file()
    assert not (repository / "engine.py").exists()


def test_a_merged_story_pull_request_finishes_the_story(env) -> None:
    session, *_ = env
    (task,) = story_tasks(env, "SG-3")
    item = story_item(task)
    item.pull_request_url = "https://github.com/o/r/pull/9"
    session.commit()
    finished = []

    class Pulls:
        merged = False

        def request(self, method, path, body=None):
            return {"merged": self.merged}

    pulls = Pulls()

    assert sync_merged_stories(session, pulls, finished.append) == []
    pulls.merged = True

    assert sync_merged_stories(session, pulls, finished.append) == ["SG-2"]
    assert finished == [item]
    assert item.pull_request_merged
    assert sync_merged_stories(session, pulls, finished.append) == []


def test_migration_adds_story_columns_to_an_old_jira_items_table(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE jira_items (id INTEGER, key TEXT)"))

    migrate_jira_item_columns(engine)
    migrate_jira_item_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("jira_items")}
    assert {"branch_name", "pull_request_url", "pull_request_merged"} <= columns
    engine.dispose()


def test_the_builder_can_resolve_a_conflict_and_the_task_is_then_delivered(env) -> None:
    session, *_ = env
    first, second = story_tasks(env, "SG-3", "SG-4")
    build(first, "engine.py", "a = 1\n")
    build(second, "engine.py", "b = 2\n")
    delivery = StoryDelivery(session, FakePublisher())
    delivery.deliver(first, story_item(first))
    assert not delivery.deliver(second, story_item(second)).ok

    build(second, "engine.py", "a = 1\nb = 2\n")

    assert delivery.deliver(second, story_item(second)).ok
    assert second.story_branch == "agent/story-sg-2"


def test_a_task_started_later_gets_the_code_already_in_the_story(env) -> None:
    session, *_ = env
    first, second = story_tasks(env, "SG-3", "SG-4")
    build(first, "engine.py")
    delivery = StoryDelivery(session, FakePublisher())
    delivery.deliver(first, story_item(first))

    delivery.start(second)

    assert (Path(second.worktree_path) / "engine.py").is_file()


def test_starting_a_task_without_a_story_does_nothing(env) -> None:
    session, milestone_id, _, root = env
    items = [work("SG-3", None, [EPIC])]
    sync_jira_items(session, milestone_id, StaticItemSource(items), root)

    StoryDelivery(session, FakePublisher()).start(session.scalar(select(Task)))


def test_the_pull_request_waits_for_a_story_issue_that_is_not_imported_yet(
    env,
) -> None:
    session, *_ = env
    (first,) = story_tasks(env, "SG-3")
    build(first, "engine.py")
    coming = {"SG-4"}
    publisher = FakePublisher()
    delivery = StoryDelivery(session, publisher, open_children=lambda item: coming)

    delivery.deliver(first, story_item(first))

    assert publisher.calls == []

    # Its issue arrives and is built and delivered; then the PR opens.
    (second,) = story_tasks(env, "SG-4")
    build(second, "walls.py")
    delivery.deliver(second, story_item(second))

    assert len(publisher.calls) == 1


def test_an_unreadable_jira_keeps_the_pull_request_back(env) -> None:
    from orchestrator.app.core.exceptions import IssueSourceError

    session, *_ = env
    (task,) = story_tasks(env, "SG-3")
    build(task, "engine.py")
    publisher = FakePublisher()

    def broken(item):
        raise IssueSourceError("down")

    StoryDelivery(session, publisher, open_children=broken).deliver(
        task, story_item(task)
    )

    assert publisher.calls == []


def test_a_pull_request_that_failed_to_open_is_retried(env, monkeypatch) -> None:
    from types import SimpleNamespace

    from orchestrator.app.jira import stories

    session, *_ = env
    (task,) = story_tasks(env, "SG-3")
    build(task, "engine.py")
    publisher = FakePublisher(url=None)
    delivery = StoryDelivery(session, publisher)
    delivery.deliver(task, story_item(task))
    item = story_item(task)

    assert item.pull_request_url is None

    publisher.url = "https://github.com/o/r/pull/9"
    monkeypatch.setattr(stories, "make_story_delivery", lambda db, settings: delivery)
    settings = SimpleNamespace(publish_branches=True, open_pull_request=True)

    assert stories.open_pending_pull_requests(session, settings) == ["SG-2"]
    assert item.pull_request_url == publisher.url
    assert stories.open_pending_pull_requests(session, settings) == []


def test_nothing_is_retried_when_pull_requests_are_off(env) -> None:
    from types import SimpleNamespace

    from orchestrator.app.jira.stories import open_pending_pull_requests

    session, *_ = env
    settings = SimpleNamespace(publish_branches=False, open_pull_request=False)

    assert open_pending_pull_requests(session, settings) == []
