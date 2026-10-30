from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from orchestrator.app.api import control_routes, task_routes
from orchestrator.app.dashboard import routes as dashboard_routes
from orchestrator.app.dashboard.service import MAX_ACTIVITY_EVENTS, build_overview
from orchestrator.app.db.models import (
    AgentRun,
    Milestone,
    Project,
    Task,
    TaskStatus,
    VerificationResult,
)
from orchestrator.app.jira.models import JiraItem


@pytest.fixture
def seeded(routes_client):
    """Create a task with the given status, runs and checks."""

    with routes_client.session_factory() as session:
        project = Project(name="dash-project")
        session.add(project)
        session.flush()
        milestone = Milestone(project_id=project.id, code="GH", title="GitHub issues")
        session.add(milestone)
        session.commit()
        milestone_id = milestone.id

    def make(status, runs=(), checks=(), **fields):
        with routes_client.session_factory() as session:
            task = Task(
                milestone_id=milestone_id,
                title=fields.pop("title", "Dashboard task"),
                status=status,
                branch_name=fields.pop("branch_name", "agent/task-1"),
                **fields,
            )
            session.add(task)
            session.flush()
            for role, run_status, tokens in runs:
                session.add(
                    AgentRun(
                        task_id=task.id,
                        agent_role=role,
                        model="sonnet",
                        effort="high",
                        status=run_status,
                        input_tokens=tokens,
                        output_tokens=tokens * 2,
                        reported_cost_usd=0.5,
                        started_at=datetime.now(UTC),
                    )
                )
            for name, passed in checks:
                session.add(
                    VerificationResult(
                        task_id=task.id, check_name=name, passed=passed, details="log"
                    )
                )
            session.commit()
            return task.id

    return make


def overview(routes_client, **kwargs):
    with routes_client.session_factory() as session:
        return build_overview(
            session,
            repo=kwargs.get("repo", "owner/repo"),
            max_concurrent_agents=2,
            polling_enabled=True,
        )


def states(task):
    return {stage.name: stage.state for stage in task.stages}


def test_running_builder_shows_remaining_stages_pending(routes_client, seeded) -> None:
    seeded(
        TaskStatus.RUNNING, runs=[("builder", "RUNNING", 10)], current_stage="builder"
    )

    task = overview(routes_client).tasks[0]

    assert states(task) == {
        "builder": "running",
        "tester": "pending",
        "checks": "pending",
        "reviewer": "pending",
        "pull_request": "pending",
    }
    assert task.actions.model_dump() == {
        "start": False,
        "pause": True,
        "resume": False,
        "cancel": True,
    }


def test_completed_task_shows_every_stage_done(routes_client, seeded) -> None:
    seeded(
        TaskStatus.COMPLETED,
        runs=[
            ("builder", "COMPLETED", 1),
            ("tester", "COMPLETED", 2),
            ("reviewer", "COMPLETED", 3),
        ],
        checks=[("unit_tests", True), ("ruff_check", True)],
        pull_request_url="https://github.com/owner/repo/pull/8",
        github_issue_number=7,
    )

    task = overview(routes_client).tasks[0]

    assert set(states(task).values()) == {"done"}
    assert task.stages[2].detail == "2/2 passed"
    assert task.issue_url == "https://github.com/owner/repo/issues/7"
    assert [branch.name for branch in task.branches] == ["agent/task-1"]
    assert task.branches[0].url == "https://github.com/owner/repo/tree/agent/task-1"
    assert task.usage.input_tokens == 6
    assert task.usage.output_tokens == 12
    assert task.usage.cost_usd == pytest.approx(1.5)
    assert not any(task.actions.model_dump().values())


def test_failed_checks_skip_the_reviewer(routes_client, seeded) -> None:
    seeded(
        TaskStatus.FAILED,
        runs=[("builder", "COMPLETED", 1), ("tester", "COMPLETED", 1)],
        checks=[("unit_tests", False), ("ruff_check", True)],
    )

    task = overview(routes_client).tasks[0]

    assert states(task) == {
        "builder": "done",
        "tester": "done",
        "checks": "failed",
        "reviewer": "skipped",
        "pull_request": "skipped",
    }
    assert task.stages[2].detail == "1/2 passed"


def test_rejected_review_is_reported_as_rejected(routes_client, seeded) -> None:
    seeded(
        TaskStatus.FAILED,
        runs=[
            ("builder", "COMPLETED", 1),
            ("tester", "COMPLETED", 1),
            ("reviewer", "COMPLETED", 1),
        ],
        checks=[("unit_tests", True)],
    )

    assert states(overview(routes_client).tasks[0])["reviewer"] == "rejected"


def test_verifying_task_shows_checks_running(routes_client, seeded) -> None:
    seeded(
        TaskStatus.VERIFYING,
        runs=[("builder", "COMPLETED", 1), ("tester", "COMPLETED", 1)],
    )

    assert states(overview(routes_client).tasks[0])["checks"] == "running"


def test_paused_and_cancelled_states(routes_client, seeded) -> None:
    seeded(TaskStatus.PAUSED, runs=[("builder", "PAUSED", 1)], current_stage="builder")
    seeded(TaskStatus.CANCELLED, runs=[("builder", "CANCELLED", 0)])

    cancelled, paused = overview(routes_client).tasks

    assert states(paused)["builder"] == "paused"
    assert paused.actions.resume is True
    assert paused.actions.pause is False
    assert states(cancelled)["builder"] == "cancelled"
    assert states(cancelled)["tester"] == "skipped"


def test_queued_task_can_be_started(routes_client, seeded) -> None:
    seeded(TaskStatus.QUEUED)

    task = overview(routes_client).tasks[0]

    assert task.actions.start is True
    assert task.actions.cancel is True
    assert set(states(task).values()) == {"pending"}
    assert task.branches == []


def test_summary_totals_and_counts(routes_client, seeded) -> None:
    seeded(TaskStatus.RUNNING, runs=[("builder", "RUNNING", 5)], title="a")
    seeded(TaskStatus.COMPLETED, runs=[("builder", "COMPLETED", 5)], title="b")
    seeded(TaskStatus.QUEUED, title="c")

    result = overview(routes_client)

    assert result.summary.status_counts == {"running": 1, "completed": 1, "queued": 1}
    assert result.summary.active_agents == 1
    assert result.summary.max_concurrent_agents == 2
    assert result.summary.usage.input_tokens == 10
    assert result.summary.usage.cost_usd == pytest.approx(1.0)
    assert [task.title for task in result.tasks] == ["c", "b", "a"]
    assert result.milestones[0].project == "dash-project"


def test_overview_without_repo_has_no_links(routes_client, seeded) -> None:
    seeded(TaskStatus.RUNNING, runs=[("builder", "RUNNING", 1)], github_issue_number=3)

    task = overview(routes_client, repo=None).tasks[0]

    assert task.issue_url is None
    assert task.branches[0].url is None


def test_overview_endpoint_and_page(routes_client, seeded, monkeypatch) -> None:
    from orchestrator.app.core.config import Settings

    routes_client.app.include_router(dashboard_routes.router)
    monkeypatch.setattr(
        dashboard_routes,
        "get_settings",
        lambda: Settings(
            _env_file=None, github_repo="owner/repo", poll_interval_seconds=30
        ),
    )
    seeded(TaskStatus.RUNNING, runs=[("builder", "RUNNING", 4)])

    data = routes_client.client.get("/dashboard/overview").json()
    page = routes_client.client.get("/dashboard")

    assert data["repo"] == "owner/repo"
    assert data["summary"]["polling_enabled"] is True
    assert data["tasks"][0]["stages"][0]["state"] == "running"
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "Agent Orchestrator" in page.text
    assert "/dashboard/overview" in page.text


def test_dashboard_page_never_injects_html() -> None:
    from pathlib import Path

    html = Path(dashboard_routes.PAGE).read_text(encoding="utf-8")

    assert "innerHTML" not in html
    assert "insertAdjacentHTML" not in html
    assert "document.write" not in html


def _dashboard_source() -> str:
    from pathlib import Path

    return Path(dashboard_routes.PAGE).read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    end = source.index("\n}\n", start)
    return source[start:end]


def test_task_card_facts_row_shows_time_after_cost() -> None:
    card = _function_body(_dashboard_source(), "renderCard")
    facts = card.index('el("div", { class: "facts" }')

    time_label = card.index('"Time "', facts)
    assert card.index('"Cost "', facts) < time_label < card.index("branches", facts)
    assert 'class: "task-time"' in card[time_label:]
    assert '"data-task-start": span.start' in card[time_label:]
    assert '"data-task-end": span.end' in card[time_label:]


def test_task_time_placeholder_and_ticking() -> None:
    source = _dashboard_source()
    span = _function_body(source, "taskSpan")
    ticker = _function_body(source, "tickDurations")

    assert 'return { start: "", end: "" };' in span
    assert 'running || end === null ? "" : ' in span
    assert 'document.querySelectorAll("strong[data-task-start]")' in ticker
    assert 'fmtDuration((end - start) / 1000) : "–"' in ticker
    assert "setInterval(tickDurations, 1000);" in source


def test_control_routes_share_status_groups() -> None:
    from orchestrator.app.db.models import CANCELLABLE_STATUSES, PAUSABLE_STATUSES

    assert control_routes.PAUSABLE_STATUSES is PAUSABLE_STATUSES
    assert control_routes.CANCELLABLE_STATUSES is CANCELLABLE_STATUSES
    assert task_routes is not None


def test_overview_reports_progress_and_cost_per_milestone(
    routes_client, seeded
) -> None:
    seeded(TaskStatus.COMPLETED, runs=[("builder", "COMPLETED", 5)], title="a")
    seeded(TaskStatus.FAILED, runs=[("builder", "COMPLETED", 5)], title="b")
    seeded(TaskStatus.QUEUED, title="c")

    result = overview(routes_client)
    milestone = result.milestones[0]

    assert result.tasks[0].milestone == "GH"
    assert milestone.code == "GH"
    assert (milestone.task_count, milestone.completed_count) == (3, 1)
    assert milestone.failed_count == 1
    assert milestone.usage.input_tokens == 10
    assert milestone.usage.cost_usd == pytest.approx(1.0)


def test_overview_summarises_each_project_and_its_board_columns(
    routes_client, seeded
) -> None:
    seeded(
        TaskStatus.RUNNING,
        runs=[("builder", "RUNNING", 5)],
        board_number=5,
        board_column="Building",
    )
    seeded(
        TaskStatus.COMPLETED,
        runs=[("builder", "COMPLETED", 5)],
        board_number=5,
        board_column="PR open",
    )
    seeded(TaskStatus.FAILED, board_number=5, board_column="Failed")

    result = overview(routes_client)
    project = result.projects[0]

    assert project.name == "dash-project"
    assert project.board_number == 5
    assert (project.task_count, project.active_count) == (3, 1)
    assert (project.completed_count, project.failed_count) == (1, 1)
    assert project.columns == {"Building": 1, "PR open": 1, "Failed": 1}
    assert project.usage.input_tokens == 10
    assert result.tasks[0].project == "dash-project"


def test_projects_without_tasks_are_not_listed(routes_client, seeded) -> None:
    seeded(TaskStatus.QUEUED)
    with routes_client.session_factory() as session:
        session.add(Project(name="empty-project"))
        session.commit()

    assert [p.name for p in overview(routes_client).projects] == ["dash-project"]


def test_dashboard_page_has_a_projects_panel_and_filter() -> None:
    source = _dashboard_source()

    assert 'id="projects"' in source
    assert 'id="project-filter"' in source
    assert "function renderProjects" in source


def test_fix_attempt_resets_later_stages_on_the_same_branch(
    routes_client, seeded
) -> None:
    seeded(
        TaskStatus.RUNNING,
        runs=[
            ("builder", "COMPLETED", 1),
            ("tester", "COMPLETED", 1),
            ("builder", "RUNNING", 1),
        ],
        checks=[("unit_tests", False)],
        current_stage="builder",
    )

    task = overview(routes_client).tasks[0]

    assert states(task) == {
        "builder": "running",
        "tester": "pending",
        "checks": "pending",
        "reviewer": "pending",
        "pull_request": "pending",
    }
    assert task.stages[0].detail.endswith("attempt 2")
    assert [branch.name for branch in task.branches] == ["agent/task-1"]


def test_summary_totals_runs_cost_and_time_per_agent_role(
    routes_client, seeded
) -> None:
    seeded(
        TaskStatus.RUNNING,
        runs=[
            ("builder", "COMPLETED", 5),
            ("builder", "COMPLETED", 5),
            ("tester", "RUNNING", 1),
        ],
    )

    roles = {role.role: role for role in overview(routes_client).summary.roles}

    assert list(roles) == ["builder", "tester"]
    assert (roles["builder"].runs, roles["builder"].active) == (2, 0)
    assert roles["builder"].cost_usd == pytest.approx(1.0)
    assert roles["builder"].input_tokens == 10
    assert (roles["tester"].runs, roles["tester"].active) == (1, 1)
    assert roles["tester"].seconds == 0


def test_run_detail_returns_the_final_message_and_how_the_run_ended(
    routes_client, seeded, monkeypatch
) -> None:
    routes_client.app.include_router(dashboard_routes.router)
    task_id = seeded(TaskStatus.COMPLETED, runs=[("builder", "COMPLETED", 3)])

    with routes_client.session_factory() as session:
        run = session.query(AgentRun).filter_by(task_id=task_id).one()
        run.result_text = "Implemented the parser."
        run.return_code = 0
        run.num_turns = 12
        run.session_id = "session-1"
        run.process_id = 4321
        session.commit()
        run_id = run.id

    listed = overview(routes_client).tasks[0].runs[0]
    detail = routes_client.client.get(f"/dashboard/runs/{run_id}").json()

    assert listed.has_result is True
    assert (listed.return_code, listed.num_turns, listed.process_id) == (0, 12, 4321)
    assert detail["result_text"] == "Implemented the parser."
    assert detail["task_title"] == "Dashboard task"
    assert detail["session_id"] == "session-1"
    assert detail["input_tokens"] == 3


def test_overview_lists_runs_without_their_message(
    routes_client, seeded, monkeypatch
) -> None:
    routes_client.app.include_router(dashboard_routes.router)
    seeded(TaskStatus.COMPLETED, runs=[("builder", "COMPLETED", 3)])

    body = routes_client.client.get("/dashboard/overview").json()
    run = body["tasks"][0]["runs"][0]

    assert run["has_result"] is False
    assert "result_text" not in run


def test_run_detail_of_an_unknown_run_is_not_found(routes_client) -> None:
    routes_client.app.include_router(dashboard_routes.router)

    assert routes_client.client.get("/dashboard/runs/9999").status_code == 404


def test_dashboard_page_has_the_agent_run_views() -> None:
    source = _dashboard_source()

    for marker in (
        'id="search"',
        'id="view-board"',
        'id="live-panel"',
        'id="status-panel"',
        'id="roles-panel"',
        'id="drawer"',
        "function openDrawer",
        "function runDetails",
        "function renderBoard",
        "/dashboard/runs/",
    ):
        assert marker in source


def test_dashboard_reads_zone_less_times_as_utc() -> None:
    source = _dashboard_source()

    assert "function parseTime(" in source
    assert "Date.parse(" not in source.replace("return Date.parse(", "")


def add_jira_items(routes_client, items):
    """Save (key, type, summary, parent_key) rows; parents must come first."""
    with routes_client.session_factory() as session:
        project = session.scalar(select(Project))
        for key, kind, summary, parent in items:
            session.add(
                JiraItem(
                    key=key,
                    type=kind,
                    summary=summary,
                    project_id=project.id,
                    parent_key=parent,
                )
            )
            session.flush()
        session.commit()


def test_a_task_carries_its_epic_and_story(routes_client, seeded) -> None:
    add_jira_items(
        routes_client,
        [
            ("SG-8", "Epic", "Gameplay variety", None),
            ("SG-9", "Story", "Bonus food", "SG-8"),
            ("SG-11", "Subtask", "Add bonus food", "SG-9"),
        ],
    )
    seeded(TaskStatus.COMPLETED, jira_key="SG-11", title="SG-11: Add bonus food")

    task = overview(routes_client).tasks[0]

    assert task.jira_type == "Subtask"
    assert [(h.key, h.type, h.title) for h in task.hierarchy] == [
        ("SG-8", "Epic", "Gameplay variety"),
        ("SG-9", "Story", "Bonus food"),
    ]


def test_work_items_list_every_issue_and_link_tasks_to_their_issue(
    routes_client, seeded
) -> None:
    add_jira_items(
        routes_client,
        [
            ("SG-8", "Epic", "Gameplay variety", None),
            ("SG-9", "Story", "Bonus food", "SG-8"),
            ("SG-11", "Subtask", "Add bonus food", "SG-9"),
        ],
    )
    task_id = seeded(TaskStatus.QUEUED, jira_key="SG-11")

    items = {item.key: item for item in overview(routes_client).work_items}

    assert set(items) == {"SG-8", "SG-9", "SG-11"}
    assert items["SG-9"].parent_key == "SG-8"
    assert items["SG-8"].task_id is None
    assert items["SG-11"].task_id == task_id
    assert items["SG-11"].title == "Add bonus food"


def test_a_task_without_a_jira_record_has_no_hierarchy(routes_client, seeded) -> None:
    seeded(TaskStatus.QUEUED)
    seeded(TaskStatus.QUEUED, jira_key="SG-77")

    result = overview(routes_client)

    assert all(task.hierarchy == [] for task in result.tasks)
    assert all(task.jira_type is None for task in result.tasks)
    assert result.work_items == []


def test_a_parent_loop_does_not_hang_the_overview(routes_client, seeded) -> None:
    add_jira_items(
        routes_client, [("A-1", "Story", "one", None), ("A-2", "Story", "two", "A-1")]
    )
    with routes_client.session_factory() as session:
        first = session.scalar(select(JiraItem).where(JiraItem.key == "A-1"))
        first.parent_key = "A-2"
        session.commit()
    seeded(TaskStatus.QUEUED, jira_key="A-2")

    task = overview(routes_client).tasks[0]

    assert [h.key for h in task.hierarchy] == ["A-1"]


def finished_run(task_id, role, started, seconds, cost=0.25, status="COMPLETED"):
    return AgentRun(
        task_id=task_id,
        agent_role=role,
        model="sonnet",
        effort="medium",
        status=status,
        reported_cost_usd=cost,
        started_at=started,
        finished_at=started + timedelta(seconds=seconds),
    )


def test_activity_lists_what_happened_newest_first(routes_client, seeded) -> None:
    task_id = seeded(TaskStatus.COMPLETED, jira_key="SG-5", title="SG-5: x")
    start = datetime.now(UTC) - timedelta(minutes=10)

    with routes_client.session_factory() as session:
        session.get(Task, task_id).created_at = start - timedelta(minutes=1)
        session.add(finished_run(task_id, "builder", start, 98))
        session.add(
            finished_run(task_id, "tester", start + timedelta(minutes=3), 45, 0.04)
        )
        session.commit()

    events = overview(routes_client).activity
    texts = [event.text for event in events]

    assert texts[0] == "Tester finished · 45s · $0.04"
    assert "Builder finished · 1m 38s · $0.25" in texts
    assert "Builder started" in texts
    assert texts[-1] == "Task added"
    assert [e.at for e in events] == sorted((e.at for e in events), reverse=True)
    assert {event.label for event in events} == {"SG-5"}
    assert {event.task_id for event in events} == {task_id}


def test_activity_marks_stopped_runs_and_never_mixes_time_zones(
    routes_client, seeded
) -> None:
    task_id = seeded(TaskStatus.CANCELLED)

    with routes_client.session_factory() as session:
        session.add(
            finished_run(task_id, "builder", datetime.now(UTC), 12, 0, "CANCELLED")
        )
        session.commit()

    events = overview(routes_client).activity

    assert any(
        e.kind == "stopped" and e.text.startswith("Builder cancelled") for e in events
    )
    assert all(event.at.tzinfo is None for event in events)


def test_checks_saved_together_are_one_activity_event(routes_client, seeded) -> None:
    passing = seeded(
        TaskStatus.COMPLETED,
        checks=[("unit_tests", True), ("ruff_check", True), ("ruff_format", True)],
        title="passing",
    )
    failing = seeded(
        TaskStatus.FAILED,
        checks=[("unit_tests", True), ("ruff_format", False)],
        title="failing",
    )

    events = overview(routes_client).activity
    by_task = {
        task_id: [
            e for e in events if e.task_id == task_id and e.kind.startswith("checks")
        ]
        for task_id in (passing, failing)
    }

    assert [(e.kind, e.text) for e in by_task[passing]] == [
        ("checks_ok", "Checks passed · 3/3")
    ]
    assert [(e.kind, e.text) for e in by_task[failing]] == [
        ("checks_failed", "Checks failed · ruff_format")
    ]


def test_activity_is_capped(routes_client, seeded) -> None:
    for number in range(MAX_ACTIVITY_EVENTS):
        seeded(TaskStatus.QUEUED, title=f"task {number}")

    assert len(overview(routes_client).activity) == MAX_ACTIVITY_EVENTS


def test_overview_endpoint_includes_hierarchy_and_activity(
    routes_client, seeded, monkeypatch
) -> None:
    from orchestrator.app.core.config import Settings

    routes_client.app.include_router(dashboard_routes.router)
    monkeypatch.setattr(
        dashboard_routes,
        "get_settings",
        lambda: Settings(_env_file=None, github_repo="owner/repo"),
    )
    add_jira_items(
        routes_client,
        [("SG-8", "Epic", "Variety", None), ("SG-11", "Subtask", "Bonus", "SG-8")],
    )
    seeded(TaskStatus.QUEUED, jira_key="SG-11")

    data = routes_client.client.get("/dashboard/overview").json()

    assert data["tasks"][0]["hierarchy"][0]["key"] == "SG-8"
    assert data["tasks"][0]["jira_type"] == "Subtask"
    assert {item["key"] for item in data["work_items"]} == {"SG-8", "SG-11"}
    assert data["activity"][0]["label"] == "SG-11"


def test_dashboard_page_has_the_work_attention_and_activity_views() -> None:
    source = _dashboard_source()

    for marker in (
        'id="view-work"',
        'id="work"',
        'id="attention-panel"',
        'id="activity-panel"',
        "function renderWork",
        "function workModel",
        "function renderAttention",
        "function attentionItems",
        "function renderActivity",
        "function crumbs",
        "function hierarchyBlock",
        "function normalize(",
        'class="layout"',
    ):
        assert marker in source


def test_dashboard_page_copes_with_an_older_server() -> None:
    source = _dashboard_source()
    normalize = _function_body(source, "normalize")

    assert "data.work_items = data.work_items || [];" in normalize
    assert "data.activity = data.activity || [];" in normalize
    assert "task.hierarchy = task.hierarchy || [];" in normalize
    assert "normalize(state.data);" in source


def test_dashboard_page_hides_stale_attention_items() -> None:
    source = _dashboard_source()

    assert "const ATTENTION_MAX_AGE_MS" in source
    assert "parseTime(item.task.created_at) >= cutoff" in source
    # A null child would be printed as the text "null" by replaceChildren.
    assert ": null)" not in _function_body(source, "renderAttention")
