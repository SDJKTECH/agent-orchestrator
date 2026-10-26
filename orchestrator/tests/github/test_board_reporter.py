from unittest.mock import MagicMock

from orchestrator.app.core.exceptions import IssueSourceError
from orchestrator.app.db.models import Milestone, Project, Task
from orchestrator.app.github.board_reporter import BoardMover


def task(**fields) -> Task:
    return Task(id=1, title="t", project_item_id="item-1", **fields)


def test_move_updates_the_card_and_remembers_the_column() -> None:
    projects = MagicMock()
    item = task()

    assert BoardMover(projects).move(item, "Building") is True

    projects.set_column.assert_called_once_with("item-1", "Building")
    assert item.board_column == "Building"


def test_each_task_is_moved_on_its_own_board() -> None:
    first, second = MagicMock(number=4), MagicMock(number=5)
    mover = BoardMover([first, second])

    mover.move(task(board_number=5), "Testing")

    second.set_column.assert_called_once_with("item-1", "Testing")
    first.set_column.assert_not_called()


def test_a_board_linked_after_start_up_is_found_when_a_card_needs_moving() -> None:
    late = MagicMock(number=6)
    refresh = MagicMock(return_value=[late])
    mover = BoardMover([MagicMock(number=4)], refresh=refresh)

    assert mover.move(task(board_number=6), "Testing") is True

    late.set_column.assert_called_once_with("item-1", "Testing")
    refresh.assert_called_once()


def test_unknown_boards_are_not_looked_up_more_than_once_in_a_while() -> None:
    refresh = MagicMock(return_value=[])
    mover = BoardMover([MagicMock(number=4), MagicMock(number=5)], refresh=refresh)

    mover.move(task(board_number=9), "Testing")
    mover.move(task(board_number=9), "Building")

    refresh.assert_called_once()


def test_a_failed_board_lookup_does_not_break_the_run() -> None:
    refresh = MagicMock(side_effect=IssueSourceError("offline"))
    mover = BoardMover([MagicMock(number=4), MagicMock(number=5)], refresh=refresh)

    assert mover.move(task(board_number=9), "Testing") is False


def test_a_task_with_no_board_number_is_left_alone_among_several_boards() -> None:
    first, second = MagicMock(number=4), MagicMock(number=5)

    assert BoardMover([first, second]).move(task(), "Testing") is False


def test_a_task_with_no_board_number_uses_the_only_board() -> None:
    only = MagicMock(number=4)

    assert BoardMover([only]).move(task(), "Testing") is True


def test_move_skips_tasks_without_a_card_or_already_in_the_column() -> None:
    projects = MagicMock()
    mover = BoardMover(projects)

    assert mover.move(Task(id=2, title="t"), "Building") is False
    assert mover.move(task(board_column="Building"), "Building") is False
    projects.set_column.assert_not_called()


def test_move_failure_is_swallowed_and_leaves_the_column_unchanged() -> None:
    projects = MagicMock()
    projects.set_column.side_effect = IssueSourceError("down")
    item = task(board_column="Queued")

    assert BoardMover(projects).move(item, "Building") is False
    assert item.board_column == "Queued"


def test_merged_pull_requests_move_their_card_to_done(routes_client) -> None:
    with routes_client.session_factory() as session:
        project = Project(name="p")
        session.add(project)
        session.flush()
        milestone = Milestone(project_id=project.id, code="M1", title="m")
        session.add(milestone)
        session.flush()
        merged = Task(
            milestone_id=milestone.id,
            title="merged",
            project_item_id="i1",
            board_column="PR open",
            pull_request_url="https://github.com/o/r/pull/7",
        )
        open_pr = Task(
            milestone_id=milestone.id,
            title="open",
            project_item_id="i2",
            board_column="PR open",
            pull_request_url="https://github.com/o/r/pull/8",
        )
        session.add_all([merged, open_pr])
        session.commit()

        pulls = MagicMock()
        pulls.request.side_effect = lambda method, path: {"merged": path.endswith("/7")}
        projects = MagicMock()

        done = BoardMover(projects, pulls).sync_merged(session)

        assert done == [merged.id]
        projects.set_column.assert_called_once_with("i1", "Done")
        assert open_pr.board_column == "PR open"
