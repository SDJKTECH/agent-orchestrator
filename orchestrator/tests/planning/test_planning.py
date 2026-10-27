import json
import subprocess
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from orchestrator.app.agents.agent_runner import AgentExecutionResult
from orchestrator.app.agents.execution_manager import ExecutionManager
from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import AgentExecutionError
from orchestrator.app.db.database import Base
from orchestrator.app.db.models import Milestone, Project, Task, TaskStatus
from orchestrator.app.github.intake import Issue
from orchestrator.app.github.poller import IntakePoller
from orchestrator.app.github.projects import BLOCKED, DONE, QUEUED
from orchestrator.app.jira.client import JiraApi
from orchestrator.app.jira.source import blockers
from orchestrator.app.planning.dependencies import (
    assign_blockers,
    get_blockers,
    is_merged,
    release_ready,
)
from orchestrator.app.planning.planner import (
    Dependency,
    DependencyPlanner,
    parse_plan,
)
from orchestrator.app.services.task_service import create_task
from orchestrator.tests.helpers import StaticIssueSource, init_repository


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'plan.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db
    engine.dispose()


def make_tasks(db, *keys, local_path="x") -> list[Task]:
    project = Project(name="p", local_path=local_path)
    milestone = Milestone(project=project, code="GH", title="m")
    tasks = [Task(milestone=milestone, title=f"{key}: t", jira_key=key) for key in keys]
    db.add_all(tasks)
    db.commit()
    return tasks


class FakeRunner:
    def __init__(self, reply=None, fail=False, code=0):
        self.reply, self.fail, self.code = reply, fail, code
        self.commands: list[list[str]] = []

    def run(self, command, *, cwd, timeout_seconds, task_id=None):
        self.commands.append(command)

        if self.fail:
            raise AgentExecutionError("boom")

        return AgentExecutionResult(
            self.code, json.dumps({"result": self.reply}), "", 1
        )


def test_parse_plan_keeps_valid_dependencies_only() -> None:
    reply = (
        'Here you go: {"B": [{"blocked_by": "A", "reason": "uses  A\\nparser"}, '
        '{"blocked_by": "B"}, {"blocked_by": "Z"}], "Q": ["A"], "A": "oops"}'
    )

    plan = parse_plan(reply, {"A", "B"})

    assert plan == {"B": [Dependency("A", "uses A parser")]}


@pytest.mark.parametrize("reply", ["no json", "{broken", "[1, 2]", "{}", ""])
def test_unusable_replies_mean_independent_tasks(reply) -> None:
    assert parse_plan(reply, {"A", "B"}) == {}


def test_planner_asks_the_planner_agent_and_returns_its_plan(tmp_path, session) -> None:
    a, b = make_tasks(session, "CSV-1", "CSV-2")
    runner = FakeRunner('{"CSV-2": [{"blocked_by": "CSV-1", "reason": "needs it"}]}')
    planner = DependencyPlanner(
        Settings(_env_file=None, repository_path=tmp_path), runner
    )

    plan = planner.plan([a, b])

    assert plan == {"CSV-2": [Dependency("CSV-1", "needs it")]}
    command = runner.commands[0]
    assert command[command.index("--agent") + 1] == "planner"
    assert "CSV-1" in command[-1] and "CSV-2" in command[-1]


def test_planner_failures_mean_independent_tasks(tmp_path, session) -> None:
    tasks = make_tasks(session, "A-1", "A-2")
    settings = Settings(_env_file=None, repository_path=tmp_path)

    assert DependencyPlanner(settings, FakeRunner(fail=True)).plan(tasks) == {}
    assert DependencyPlanner(settings, FakeRunner("x", code=1)).plan(tasks) == {}


def test_planner_is_not_run_for_a_single_task(tmp_path, session) -> None:
    runner = FakeRunner("{}")
    planner = DependencyPlanner(
        Settings(_env_file=None, repository_path=tmp_path), runner
    )

    assert planner.plan(make_tasks(session, "A-1")) == {}
    assert runner.commands == []


def test_dependents_are_blocked_and_the_reason_is_kept(session) -> None:
    t1, t2, t3 = make_tasks(session, "T-1", "T-2", "T-3")

    edges = assign_blockers(
        session,
        [t1, t2, t3],
        {"T-3": [Dependency("T-2", "uses its parser")]},
        {},
    )

    assert [(e.blocked.jira_key, e.blocker.jira_key, e.inferred) for e in edges] == [
        ("T-3", "T-2", True)
    ]
    assert (t1.status, t2.status) == (TaskStatus.QUEUED, TaskStatus.QUEUED)
    assert t3.status == TaskStatus.BLOCKED
    assert get_blockers(t3) == [{"task_id": t2.id, "reason": "uses its parser"}]


def test_jira_links_count_and_are_not_reported_as_inferred(session) -> None:
    t1, t2 = make_tasks(session, "T-1", "T-2")

    edges = assign_blockers(session, [t2], {}, {t2.id: ["T-1"]})

    assert [e.inferred for e in edges] == [False]
    assert t2.status == TaskStatus.BLOCKED


def test_a_cycle_is_refused_and_the_task_still_runs(session) -> None:
    t1, t2 = make_tasks(session, "T-1", "T-2")

    edges = assign_blockers(
        session,
        [t1, t2],
        {"T-1": [Dependency("T-2", "")], "T-2": [Dependency("T-1", "")]},
        {},
    )

    assert len(edges) == 1
    blocked = [t for t in (t1, t2) if t.status == TaskStatus.BLOCKED]
    assert len(blocked) == 1


def test_merged_unknown_and_self_blockers_are_ignored(session) -> None:
    done, new = make_tasks(session, "T-1", "T-2")
    done.status = TaskStatus.COMPLETED
    done.board_column = DONE

    edges = assign_blockers(
        session,
        [new],
        {"T-2": [Dependency("T-1", ""), Dependency("T-2", ""), Dependency("X-9", "")]},
        {},
    )

    assert edges == [] and new.status == TaskStatus.QUEUED
    assert is_merged(done)


def test_a_completed_task_with_an_open_pull_request_is_not_merged() -> None:
    task = Task(
        status=TaskStatus.COMPLETED, pull_request_url="u", board_column="PR open"
    )

    assert not is_merged(task)
    assert is_merged(Task(status=TaskStatus.COMPLETED))


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def test_a_released_task_is_cut_from_the_merged_remote_main(tmp_path, session) -> None:
    repo = tmp_path / "repo"
    init_repository(repo)
    remote = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(repo), str(remote)],
        check=True,
        capture_output=True,
    )
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "fetch", "origin")

    project = Project(name="p", local_path=str(repo))
    milestone = Milestone(project=project, code="GH", title="m")
    session.add(milestone)
    session.commit()
    blocker = create_task(
        session, milestone.id, "blocker", worktrees_root=tmp_path / "wt"
    )
    waiting = create_task(
        session, milestone.id, "waiting", worktrees_root=tmp_path / "wt"
    )
    waiting.status = TaskStatus.BLOCKED
    waiting.blocked_by = json.dumps([{"task_id": blocker.id, "reason": "r"}])
    session.commit()

    assert release_ready(session, repo, "origin", "main") == []
    assert waiting.status == TaskStatus.BLOCKED

    # The blocker's work is merged on the remote by someone else.
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", str(remote), str(other)], check=True, capture_output=True
    )
    git(other, "config", "user.name", "T")
    git(other, "config", "user.email", "t@example.com")
    (other / "parser.py").write_text("x = 1\n", encoding="utf-8")
    git(other, "add", "parser.py")
    git(other, "commit", "-m", "merge blocker")
    git(other, "push", "origin", "main")
    blocker.status, blocker.board_column = TaskStatus.COMPLETED, DONE
    session.commit()

    released = release_ready(session, repo, "origin", "main")

    assert released == [waiting]
    assert waiting.status == TaskStatus.QUEUED
    assert (tmp_path / "wt" / f"task-{waiting.id}" / "parser.py").is_file()
    assert not (repo / "parser.py").exists()


def test_poller_holds_a_dependent_and_runs_the_rest(tmp_path) -> None:
    repository = tmp_path / "source"
    init_repository(repository)
    engine = create_engine(f"sqlite:///{(tmp_path / 'poll.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    manager = ExecutionManager(max_workers=3)
    ran: list[int] = []
    board = MagicMock()
    planner = MagicMock()
    planner.plan.return_value = {"T-3": [Dependency("T-2", "uses it")]}
    source = StaticIssueSource(
        [
            Issue(number=0, title=f"T-{n}: x", jira_key=f"T-{n}", board_title="P")
            for n in (1, 2, 3)
        ]
    )
    poller = IntakePoller(
        settings=Settings(
            _env_file=None, repository_path=repository, poll_interval_seconds=1
        ),
        session_factory=factory,
        manager=manager,
        run_pipeline=ran.append,
        source=source,
        board=board,
        planner=planner,
    )

    scheduled = poller.poll_once()
    manager.shutdown(wait=True)
    engine.dispose()

    with factory() as db:
        tasks = {t.jira_key: t for t in db.scalars(select(Task))}

    assert tasks["T-3"].status == TaskStatus.BLOCKED
    assert sorted(scheduled) == sorted([tasks["T-1"].id, tasks["T-2"].id])
    moves = {c.args[0].jira_key: c.args[1] for c in board.move.call_args_list}
    assert moves == {"T-1": QUEUED, "T-2": QUEUED, "T-3": BLOCKED}
    board.link_blocks.assert_called_once()
    assert board.link_blocks.call_args.args[0].jira_key == "T-3"


def test_jira_blocked_by_links_are_read() -> None:
    fields = {
        "issuelinks": [
            {"type": {"name": "Blocks"}, "inwardIssue": {"key": "CSV-1"}},
            {"type": {"name": "Blocks"}, "outwardIssue": {"key": "CSV-9"}},
            {"type": {"name": "Relates"}, "inwardIssue": {"key": "CSV-5"}},
        ]
    }

    assert blockers(fields) == ["CSV-1"]
    assert blockers({}) == []


def test_creating_a_jira_link_names_the_blocker_as_outward() -> None:
    calls = []
    api = JiraApi("https://x.atlassian.net", "me", "t", lambda *a: calls.append(a))

    api.link_blocks("CSV-1", "CSV-2")

    method, path, body = calls[0]
    assert (method, path) == ("POST", "/issueLink")
    assert body["outwardIssue"] == {"key": "CSV-1"}
    assert body["inwardIssue"] == {"key": "CSV-2"}
