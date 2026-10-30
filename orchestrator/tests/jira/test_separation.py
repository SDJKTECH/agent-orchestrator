from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import orchestrator.app.github as github_package
from orchestrator.app.api import intake_routes
from orchestrator.app.boards import make_board
from orchestrator.app.core.config import Settings
from orchestrator.app.db.database import Base
from orchestrator.app.db.models import Milestone, Project, Task
from orchestrator.app.github.board_reporter import BoardMover
from orchestrator.app.github.merged import sync_merged_tasks
from orchestrator.app.github.projects import PR_OPEN
from orchestrator.tests.helpers import init_repository
from orchestrator.tests.jira.test_importer import EPIC, STORY, StaticItemSource, work


def test_the_github_package_does_not_import_jira() -> None:
    for path in Path(github_package.__file__).parent.glob("*.py"):
        assert "app.jira" not in path.read_text(encoding="utf-8"), path.name


def test_without_jira_the_github_board_is_used() -> None:
    assert make_board(Settings(_env_file=None)) is None


def test_the_github_merge_check_ignores_jira_tasks() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    pulls = MagicMock()
    pulls.request.return_value = {"merged": True}
    move = MagicMock(return_value=True)

    with sessionmaker(bind=engine)() as session:
        milestone = Milestone(project=Project(name="p"), code="GH", title="m")
        github_task = Task(
            milestone=milestone,
            title="gh",
            board_column=PR_OPEN,
            pull_request_url="https://github.com/o/r/pull/4",
        )
        session.add_all(
            [
                github_task,
                Task(
                    milestone=milestone,
                    title="PROJ-1: t",
                    jira_key="PROJ-1",
                    board_column=PR_OPEN,
                    pull_request_url="https://github.com/o/r/pull/3",
                ),
            ]
        )
        session.commit()

        done = sync_merged_tasks(session, pulls, move)

        assert done == [github_task.id]
        pulls.request.assert_called_once_with("GET", "/pulls/4")


def test_a_card_mover_has_no_dependency_links_to_write() -> None:
    assert not hasattr(BoardMover, "link_blocks")


def test_the_sync_endpoint_imports_jira_tasks_with_their_parents(
    routes_client, tmp_path, monkeypatch
) -> None:
    repository = tmp_path / "source"
    init_repository(repository)
    client = routes_client.client
    project_id = client.post(
        "/projects", json={"name": "Sync", "local_path": str(repository)}
    ).json()["id"]
    milestone = client.post(
        f"/projects/{project_id}/milestones", json={"code": "M1", "title": "Sync"}
    ).json()
    monkeypatch.setattr(
        intake_routes,
        "get_settings",
        lambda: Settings(
            _env_file=None, repository_path=repository, issue_source="jira"
        ),
    )
    routes_client.app.dependency_overrides[intake_routes.get_issue_source] = lambda: (
        StaticItemSource([work("SG-3", "SG-2", [EPIC, STORY])])
    )

    first = client.post(f"/milestones/{milestone['id']}/sync")
    second = client.post(f"/milestones/{milestone['id']}/sync")

    assert first.status_code == 200
    imported = first.json()["imported"]
    assert [task["title"] for task in imported] == ["SG-3: SG-3 summary"]
    assert imported[0]["github_issue_number"] is None
    assert second.json()["imported"] == []
    assert second.json()["skipped_issue_numbers"] == ["SG-3"]
