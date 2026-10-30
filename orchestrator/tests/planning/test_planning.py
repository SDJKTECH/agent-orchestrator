import json
import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.app.agents.agent_runner import AgentExecutionResult
from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import AgentExecutionError
from orchestrator.app.db.database import Base
from orchestrator.app.db.models import Milestone, Project, Task, TaskStatus
from orchestrator.app.github.projects import DONE
from orchestrator.app.jira.client import JiraApi
from orchestrator.app.jira.source import blockers
from orchestrator.app.planning.dependencies import (
    assign_blockers,
    get_blockers,
    is_merged,
    open_tasks,
    release_ready,
)
from orchestrator.app.planning.planner import (
    Dependency,
    DependencyPlanner,
    named_dependencies,
    parse_plan,
)
from orchestrator.app.services.task_service import create_task
from orchestrator.tests.helpers import init_repository


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


def test_a_task_that_names_another_task_depends_on_it(session) -> None:
    a, b, c = make_tasks(session, "SG-1", "SG-2", "SG-3")
    a.title, b.title, c.title = (
        "SG-1: Add the core module",
        "SG-2: Add a window",
        "SG-3: Add scores",
    )
    b.description = 'Using the "Add the core module" task, build the window.'
    c.description = (
        "Extends the 'add a window' task and the \"Add the core module\" task."
    )

    plan = named_dependencies([a, b, c])

    assert [d.blocker for d in plan["SG-2"]] == ["SG-1"]
    assert [d.blocker for d in plan["SG-3"]] == ["SG-1", "SG-2"]
    assert "SG-1" not in plan


def test_named_dependencies_still_apply_when_the_planner_says_none(
    tmp_path, session
) -> None:
    a, b = make_tasks(session, "SG-1", "SG-2")
    a.title, b.title = "SG-1: Add the core module", "SG-2: Add a window"
    b.description = 'Needs the "Add the core module" task.'
    settings = Settings(_env_file=None, repository_path=tmp_path)

    for runner in (FakeRunner("{}"), FakeRunner(fail=True), FakeRunner("x", code=1)):
        plan = DependencyPlanner(settings, runner).plan([a, b])

        assert [d.blocker for d in plan["SG-2"]] == ["SG-1"]


def test_a_dependency_found_twice_is_listed_once(tmp_path, session) -> None:
    a, b = make_tasks(session, "SG-1", "SG-2")
    a.title, b.title = "SG-1: Add the core module", "SG-2: Add a window"
    b.description = 'Needs the "Add the core module" task.'
    runner = FakeRunner('{"SG-2": [{"blocked_by": "SG-1", "reason": "uses it"}]}')

    plan = DependencyPlanner(
        Settings(_env_file=None, repository_path=tmp_path), runner
    ).plan([a, b])

    assert plan == {"SG-2": [Dependency("SG-1", "uses it")]}


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


def test_jira_mode_ignores_tasks_that_did_not_come_from_jira(session) -> None:
    (jira,) = make_tasks(session, "CSV-1")
    old = Task(
        milestone=jira.milestone,
        title="old",
        status=TaskStatus.COMPLETED,
        pull_request_url="u",
        board_column="PR open",
    )
    session.add(old)
    session.commit()

    assert open_tasks(session, "x") == [jira, old]
    assert open_tasks(session, "x", jira_only=True) == [jira]


def test_a_blocker_that_is_not_a_jira_task_does_not_hold_a_jira_task(
    tmp_path, session
) -> None:
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
    old = create_task(session, milestone.id, "old", worktrees_root=tmp_path / "wt")
    old.status, old.pull_request_url, old.board_column = (
        TaskStatus.COMPLETED,
        "https://gh/pull/1",
        "PR open",
    )
    waiting = create_task(
        session, milestone.id, "CSV-8", worktrees_root=tmp_path / "wt", jira_key="CSV-8"
    )
    waiting.status = TaskStatus.BLOCKED
    waiting.blocked_by = json.dumps([{"task_id": old.id, "reason": "r"}])
    session.commit()

    assert release_ready(session, repo, "origin", "main") == []

    released = release_ready(session, repo, "origin", "main", jira_only=True)

    assert released == [waiting]
    assert waiting.status == TaskStatus.QUEUED


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


def test_creating_a_jira_link_names_the_blocker_as_inward() -> None:
    calls = []
    api = JiraApi("https://x.atlassian.net", "me", "t", lambda *a: calls.append(a))

    api.link_blocks("CSV-1", "CSV-2")

    method, path, body = calls[0]
    assert (method, path) == ("POST", "/issueLink")
    assert body["inwardIssue"] == {"key": "CSV-1"}
    assert body["outwardIssue"] == {"key": "CSV-2"}
