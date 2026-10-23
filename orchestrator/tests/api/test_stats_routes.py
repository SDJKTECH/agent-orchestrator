import pytest
from sqlalchemy import func, select

from orchestrator.app.db.models import AgentRun, Milestone, Project, Task, TaskStatus


def add_task(session, milestone_id: int, status: TaskStatus) -> int:
    task = Task(milestone_id=milestone_id, title="Stats task", status=status)
    session.add(task)
    session.flush()
    return task.id


def add_run(
    session,
    task_id: int,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    reported_cost_usd: float | None = None,
) -> None:
    session.add(
        AgentRun(
            task_id=task_id,
            status="COMPLETED",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reported_cost_usd=reported_cost_usd,
        )
    )


def test_stats_on_empty_database_returns_zeros(routes_client) -> None:
    response = routes_client.client.get("/stats")

    assert response.status_code == 200
    assert response.json() == {
        "total_tasks": 0,
        "tasks_by_status": {},
        "total_runs": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_cost_usd": 0.0,
        "milestones": [],
    }


def test_stats_counts_tasks_by_status(routes_client, milestone_id) -> None:
    statuses = [TaskStatus.COMPLETED] * 3 + [TaskStatus.FAILED] * 2
    statuses += [TaskStatus.CANCELLED] * 3
    with routes_client.session_factory() as session:
        for status in statuses:
            add_task(session, milestone_id, status)
        session.commit()

    data = routes_client.client.get("/stats").json()

    assert data["total_tasks"] == 8
    assert data["tasks_by_status"] == {"completed": 3, "failed": 2, "cancelled": 3}


def test_stats_omits_statuses_without_tasks(routes_client, milestone_id) -> None:
    with routes_client.session_factory() as session:
        add_task(session, milestone_id, TaskStatus.RUNNING)
        add_task(session, milestone_id, TaskStatus.QUEUED)
        session.commit()

    data = routes_client.client.get("/stats").json()

    assert data["total_tasks"] == 2
    assert data["tasks_by_status"] == {"running": 1, "queued": 1}
    assert "completed" not in data["tasks_by_status"]


def test_stats_sums_tokens_and_cost_across_runs(routes_client, milestone_id) -> None:
    with routes_client.session_factory() as session:
        first = add_task(session, milestone_id, TaskStatus.COMPLETED)
        second = add_task(session, milestone_id, TaskStatus.FAILED)
        add_run(
            session,
            first,
            input_tokens=100,
            output_tokens=50,
            reported_cost_usd=0.25,
        )
        add_run(
            session,
            first,
            input_tokens=200,
            output_tokens=75,
            reported_cost_usd=0.10,
        )
        # Missing cost is treated as 0 but the run still counts as a run.
        add_run(session, second, input_tokens=5, output_tokens=5)
        session.commit()

    data = routes_client.client.get("/stats").json()

    assert data["total_runs"] == 3
    assert data["input_tokens"] == 305
    assert data["output_tokens"] == 130
    assert data["total_cost_usd"] == pytest.approx(0.35)


def test_stats_with_runs_but_no_cost_reports_zero_cost(
    routes_client, milestone_id
) -> None:
    with routes_client.session_factory() as session:
        task_id = add_task(session, milestone_id, TaskStatus.COMPLETED)
        add_run(session, task_id, input_tokens=10, output_tokens=20)
        session.commit()

    data = routes_client.client.get("/stats").json()

    assert data["total_runs"] == 1
    assert data["input_tokens"] == 10
    assert data["output_tokens"] == 20
    assert data["total_cost_usd"] == 0.0


def test_stats_groups_tasks_and_cost_by_milestone(routes_client) -> None:
    with routes_client.session_factory() as session:
        project = Project(name="stats-project")
        session.add(project)
        session.flush()
        m6 = Milestone(
            project_id=project.id,
            code="M6",
            title="M6 - Token and Cost Tracking",
        )
        m7 = Milestone(project_id=project.id, code="M7", title="M7 - Dashboard")
        session.add_all([m6, m7])
        session.flush()

        m6_first = add_task(session, m6.id, TaskStatus.COMPLETED)
        m6_second = add_task(session, m6.id, TaskStatus.FAILED)
        add_task(session, m6.id, TaskStatus.QUEUED)  # no runs, cost stays 0
        m7_task = add_task(session, m7.id, TaskStatus.COMPLETED)

        add_run(session, m6_first, input_tokens=1, reported_cost_usd=0.12)
        add_run(session, m6_first, input_tokens=1, reported_cost_usd=0.10)
        add_run(session, m6_second, input_tokens=1, reported_cost_usd=0.5)
        add_run(session, m7_task, input_tokens=1, reported_cost_usd=0.07)
        session.commit()

    data = routes_client.client.get("/stats").json()

    assert data["total_tasks"] == 4
    assert data["total_runs"] == 4
    assert data["total_cost_usd"] == pytest.approx(0.79)
    assert data["milestones"] == [
        {
            "code": "M6",
            "title": "M6 - Token and Cost Tracking",
            "task_count": 3,
            "cost_usd": pytest.approx(0.72),
        },
        {
            "code": "M7",
            "title": "M7 - Dashboard",
            "task_count": 1,
            "cost_usd": pytest.approx(0.07),
        },
    ]


def test_stats_does_not_write_to_the_database(routes_client, milestone_id) -> None:
    with routes_client.session_factory() as session:
        task_id = add_task(session, milestone_id, TaskStatus.COMPLETED)
        add_run(session, task_id, input_tokens=3, reported_cost_usd=0.01)
        session.commit()

    def row_counts() -> tuple[int, int, int]:
        with routes_client.session_factory() as session:
            return tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (Task, AgentRun, Milestone)
            )

    before = row_counts()
    first = routes_client.client.get("/stats")
    second = routes_client.client.get("/stats")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert row_counts() == before
