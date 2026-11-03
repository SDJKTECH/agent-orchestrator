# ruff: noqa: F811  (the env fixture is shared from the Story tests)
import pytest

from orchestrator.app.core.exceptions import IssueSourceError
from orchestrator.app.db.models import TaskStatus
from orchestrator.app.jira.client import JiraApi
from orchestrator.app.jira.models import JiraItem
from orchestrator.app.jira.progress import Move, sync_progress
from orchestrator.app.jira.stories import story_item
from orchestrator.app.jira.tracker import DEFAULT_STATUS_MAP, JiraTracker
from orchestrator.tests.jira.test_jira import FakeJira
from orchestrator.tests.jira.test_stories import env, story_tasks  # noqa: F401


class RecordingJira:
    """Stands in for the Jira client: remembers moves, answers the Epic search."""

    def __init__(self, unfinished=None, broken=False):
        self.unfinished = unfinished or []
        self.broken = broken

    def search(self, jql, limit=50):
        if self.broken:
            raise IssueSourceError("down")
        return self.unfinished


class StatusSetter:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def __call__(self, key, target):
        self.calls.append((key, target))
        return self.ok


def items(session):
    return {item.key: item for item in session.query(JiraItem)}


def run(session, api=None, setter=None, status_map=None):
    setter = setter or StatusSetter()
    moves = sync_progress(
        session,
        api or RecordingJira(),
        DEFAULT_STATUS_MAP if status_map is None else status_map,
        setter,
    )
    return moves, setter


def test_nothing_is_moved_before_a_subtask_starts(env) -> None:
    session, *_ = env
    story_tasks(env, "SG-3", "SG-4")

    moves, setter = run(session)

    assert moves == [] and setter.calls == []


def test_the_story_and_epic_start_when_a_subtask_starts(env) -> None:
    session, *_ = env
    first, _ = story_tasks(env, "SG-3", "SG-4")
    first.status = TaskStatus.RUNNING

    moves, _ = run(session)

    assert moves == [Move("SG-2", "In Progress"), Move("SG-1", "In Progress")]
    assert items(session)["SG-2"].jira_status == "In Progress"


def test_a_story_is_moved_once_not_on_every_poll(env) -> None:
    session, *_ = env
    first, _ = story_tasks(env, "SG-3", "SG-4")
    first.status = TaskStatus.RUNNING
    run(session)

    moves, setter = run(session)

    assert moves == [] and setter.calls == []


def test_a_queued_or_blocked_subtask_has_not_started(env) -> None:
    session, *_ = env
    first, second = story_tasks(env, "SG-3", "SG-4")
    first.status, second.status = TaskStatus.QUEUED, TaskStatus.BLOCKED

    assert run(session)[0] == []


def test_a_cancelled_subtask_does_not_start_the_story(env) -> None:
    session, *_ = env
    (task,) = story_tasks(env, "SG-3")
    task.status = TaskStatus.CANCELLED

    assert run(session)[0] == []


def test_the_story_is_in_review_when_its_pull_request_is_open(env) -> None:
    session, *_ = env
    (task,) = story_tasks(env, "SG-3")
    task.status = TaskStatus.COMPLETED
    story_item(task).pull_request_url = "https://github.com/o/r/pull/9"

    moves, _ = run(session)

    assert Move("SG-2", "In Review") in moves
    # The Epic is under way, not finished.
    assert items(session)["SG-1"].jira_status == "In Progress"


def merged_story(env):
    (task,) = story_tasks(env, "SG-3")
    task.status = TaskStatus.COMPLETED
    item = story_item(task)
    item.pull_request_url = "https://github.com/o/r/pull/9"
    item.pull_request_merged = True
    return task


def test_a_merged_story_is_done_and_so_is_an_epic_with_nothing_left(env) -> None:
    session, *_ = env
    merged_story(env)

    moves, _ = run(session, RecordingJira(unfinished=[]))

    assert moves == [Move("SG-2", "Done"), Move("SG-1", "Done")]


def test_the_epic_stays_open_while_jira_has_an_unfinished_story(env) -> None:
    session, *_ = env
    merged_story(env)

    moves, _ = run(session, RecordingJira(unfinished=[{"key": "SG-9"}]))

    assert moves == [Move("SG-2", "Done"), Move("SG-1", "In Progress")]


def test_the_epic_is_not_finished_when_jira_cannot_be_asked(env) -> None:
    session, *_ = env
    merged_story(env)

    moves, _ = run(session, RecordingJira(broken=True))

    assert Move("SG-1", "Done") not in moves


def test_the_epic_is_not_finished_while_one_of_its_stories_is_not_merged(env) -> None:
    session, milestone_id, _, root = env
    from orchestrator.app.jira.importer import sync_jira_items
    from orchestrator.app.jira.source import JiraRecord
    from orchestrator.tests.jira.test_importer import (
        EPIC,
        StaticItemSource,
        work,
    )

    other = JiraRecord("SG-5", "Story", "Scores", "", None, "SG-1")
    sync_jira_items(
        session,
        milestone_id,
        StaticItemSource([work("SG-6", "SG-5", [EPIC, other])]),
        root,
    )
    merged_story(env)

    moves, _ = run(session, RecordingJira(unfinished=[]))

    assert Move("SG-1", "Done") not in moves


def test_a_move_jira_refused_is_tried_again_next_poll(env) -> None:
    session, *_ = env
    first, _ = story_tasks(env, "SG-3", "SG-4")
    first.status = TaskStatus.RUNNING

    moves, setter = run(session, setter=StatusSetter(ok=False))

    assert moves == [] and items(session)["SG-2"].jira_status is None

    moves, _ = run(session)

    assert Move("SG-2", "In Progress") in moves


def test_a_status_the_map_leaves_out_is_not_moved_to(env) -> None:
    session, *_ = env
    first, _ = story_tasks(env, "SG-3", "SG-4")
    first.status = TaskStatus.RUNNING

    moves, setter = run(session, status_map={})

    assert moves == [] and setter.calls == []


def test_without_a_jira_client_an_epic_is_never_finished(env) -> None:
    session, *_ = env
    merged_story(env)
    setter = StatusSetter()

    moves = sync_progress(session, None, DEFAULT_STATUS_MAP, setter)

    assert Move("SG-2", "Done") in moves and Move("SG-1", "Done") not in moves


@pytest.mark.parametrize("column", ["Building", "PR open", "Done"])
def test_every_column_a_story_reaches_has_a_default_status(column) -> None:
    assert column in DEFAULT_STATUS_MAP


def test_the_tracker_moves_the_story_in_jira_on_each_poll(env) -> None:
    session, *_ = env
    first, _ = story_tasks(env, "SG-3", "SG-4")
    first.status = TaskStatus.RUNNING
    transport = FakeJira()
    tracker = JiraTracker(JiraApi("https://x.test", "e", "t", transport))

    tracker.sync_merged(session)

    posted = [p for m, p, _ in transport.calls if m == "POST"]
    assert "/issue/SG-2/transitions" in posted
    assert "/issue/SG-1/transitions" in posted


class DeletedInJira(FakeJira):
    """A Jira where the issues were deleted: reading one answers 404."""

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if "/issue/" in path and method != "POST":
            raise IssueSourceError(f"Jira API {method} {path} failed with 404: gone")
        return None


def test_a_story_deleted_in_jira_is_not_asked_about_again(env) -> None:
    session, *_ = env
    first, _ = story_tasks(env, "SG-3", "SG-4")
    first.status = TaskStatus.RUNNING
    transport = DeletedInJira()
    tracker = JiraTracker(JiraApi("https://x.test", "e", "t", transport))

    tracker.sync_merged(session)
    asked = len(transport.calls)
    tracker.sync_merged(session)
    tracker.sync_merged(session)

    # The Story and the Epic were each asked about once, then left alone.
    assert asked == 2
    assert len(transport.calls) == asked
    assert set(tracker._gone) == {"SG-2", "SG-1"}


def test_other_jira_errors_are_still_retried(env) -> None:
    session, *_ = env
    first, _ = story_tasks(env, "SG-3", "SG-4")
    first.status = TaskStatus.RUNNING
    transport = FakeJira(fail=True)
    tracker = JiraTracker(JiraApi("https://x.test", "e", "t", transport))

    tracker.sync_merged(session)
    asked = len(transport.calls)
    tracker.sync_merged(session)

    assert asked > 0 and len(transport.calls) == 2 * asked
    assert tracker._gone == {}


def test_an_issue_reported_missing_is_asked_about_again_later(env, monkeypatch) -> None:
    from orchestrator.app.jira import tracker as tracker_module

    session, *_ = env
    first, _ = story_tasks(env, "SG-3", "SG-4")
    first.status = TaskStatus.RUNNING
    transport = DeletedInJira()
    tracker = JiraTracker(JiraApi("https://x.test", "e", "t", transport))
    now = [1000.0]
    monkeypatch.setattr(tracker_module.time, "monotonic", lambda: now[0])

    tracker.sync_merged(session)
    asked = len(transport.calls)
    now[0] += tracker_module.MISSING_RETRY_SECONDS - 1
    tracker.sync_merged(session)

    assert len(transport.calls) == asked

    # A login that was broken and is now fixed gets noticed without a restart.
    now[0] += 2
    tracker.sync_merged(session)

    assert len(transport.calls) == 2 * asked


def run_with_jira_status(session, actual, setter=None):
    setter = setter or StatusSetter()
    moves = sync_progress(
        session,
        RecordingJira(unfinished=[]),
        DEFAULT_STATUS_MAP,
        setter,
        lambda key: actual,
    )
    return moves, setter


def test_an_issue_already_done_in_jira_is_never_moved_back(env) -> None:
    session, *_ = env
    first, _ = story_tasks(env, "SG-3", "SG-4")
    first.status = TaskStatus.RUNNING

    moves, setter = run_with_jira_status(session, "Done")

    assert moves == [] and setter.calls == []
    # What Jira really says is remembered, so it is not asked again.
    assert items(session)["SG-2"].jira_status == "Done"


def test_an_issue_further_along_than_the_target_is_left_alone(env) -> None:
    session, *_ = env
    (task,) = story_tasks(env, "SG-3")
    task.status = TaskStatus.RUNNING

    moves, setter = run_with_jira_status(session, "In Review")

    assert setter.calls == []


def test_an_issue_behind_its_target_is_still_moved_forward(env) -> None:
    session, *_ = env
    first, _ = story_tasks(env, "SG-3", "SG-4")
    first.status = TaskStatus.RUNNING

    moves, _ = run_with_jira_status(session, "To Do")

    assert Move("SG-2", "In Progress") in moves


def test_an_unreadable_status_does_not_stop_the_move(env) -> None:
    session, *_ = env
    first, _ = story_tasks(env, "SG-3", "SG-4")
    first.status = TaskStatus.RUNNING

    moves, _ = run_with_jira_status(session, None)

    assert Move("SG-2", "In Progress") in moves


def test_the_tracker_does_not_reopen_a_story_that_is_done_in_jira(env) -> None:
    session, *_ = env
    first, _ = story_tasks(env, "SG-3", "SG-4")
    first.status = TaskStatus.RUNNING
    transport = FakeJira(status="Done")
    tracker = JiraTracker(JiraApi("https://x.test", "e", "t", transport))

    tracker.sync_merged(session)

    assert [p for m, p, _ in transport.calls if m == "POST"] == []
