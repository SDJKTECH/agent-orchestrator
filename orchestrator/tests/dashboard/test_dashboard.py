from datetime import UTC, datetime

import pytest

from orchestrator.app.api import control_routes, task_routes
from orchestrator.app.dashboard import routes as dashboard_routes
from orchestrator.app.dashboard.service import build_overview
from orchestrator.app.db.models import (
    AgentRun,
    Milestone,
    Project,
    Task,
    TaskStatus,
    VerificationResult,
)


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
