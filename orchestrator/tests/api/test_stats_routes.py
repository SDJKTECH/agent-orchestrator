from orchestrator.app.db.models import AgentRun, Milestone, Project, Task, TaskStatus


def _seed_project(session_factory) -> int:
    with session_factory() as session:
        project = Project(name="stats-project")
        session.add(project)
        session.commit()
        return project.id


def _add_milestone(session_factory, project_id: int, code: str, title: str) -> int:
    with session_factory() as session:
        milestone = Milestone(project_id=project_id, code=code, title=title)
        session.add(milestone)
        session.commit()
        return milestone.id


def _add_task(
    session_factory,
    milestone_id: int,
    status: TaskStatus,
    title: str = "task",
) -> int:
    with session_factory() as session:
        task = Task(milestone_id=milestone_id, title=title, status=status)
        session.add(task)
        session.commit()
        return task.id


def _add_run(
    session_factory,
    task_id: int,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    reported_cost_usd: float | None = None,
) -> None:
    with session_factory() as session:
        session.add(
            AgentRun(
                task_id=task_id,
                agent_role="builder",
                status="COMPLETED",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reported_cost_usd=reported_cost_usd,
            )
        )
        session.commit()


def _count_rows(session_factory, model) -> int:
    with session_factory() as session:
        return len(session.query(model).all())


EMPTY_STATS = {
    "total_tasks": 0,
    "tasks_by_status": {},
    "total_runs": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "total_cost_usd": 0.0,
    "milestones": [],
}


def test_stats_empty_database_returns_zeros(routes_client):
    response = routes_client.client.get("/stats")

    assert response.status_code == 200
    assert response.json() == EMPTY_STATS


def test_stats_counts_tasks_by_status(routes_client):
    sf = routes_client.session_factory
    project_id = _seed_project(sf)
    milestone_id = _add_milestone(sf, project_id, "M1", "Routes")

    for status in (TaskStatus.COMPLETED,) * 3:
        _add_task(sf, milestone_id, status)
    for status in (TaskStatus.FAILED,) * 2:
        _add_task(sf, milestone_id, status)
    for status in (TaskStatus.CANCELLED,) * 3:
        _add_task(sf, milestone_id, status)

    response = routes_client.client.get("/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["total_tasks"] == 8
    assert body["tasks_by_status"] == {"completed": 3, "failed": 2, "cancelled": 3}
    # No runs were created, so run/token/cost totals stay at zero.
    assert body["total_runs"] == 0
    assert body["input_tokens"] == 0
    assert body["output_tokens"] == 0
    assert body["total_cost_usd"] == 0.0


def test_stats_omits_statuses_without_tasks(routes_client):
    sf = routes_client.session_factory
    project_id = _seed_project(sf)
    milestone_id = _add_milestone(sf, project_id, "M1", "Routes")
    _add_task(sf, milestone_id, TaskStatus.QUEUED)
    _add_task(sf, milestone_id, TaskStatus.RUNNING)

    body = routes_client.client.get("/stats").json()

    assert body["total_tasks"] == 2
    assert body["tasks_by_status"] == {"queued": 1, "running": 1}
    assert "completed" not in body["tasks_by_status"]


def test_stats_sums_tokens_and_cost_across_runs(routes_client):
    sf = routes_client.session_factory
    project_id = _seed_project(sf)
    milestone_id = _add_milestone(sf, project_id, "M1", "Routes")
    task_a = _add_task(sf, milestone_id, TaskStatus.COMPLETED)
    task_b = _add_task(sf, milestone_id, TaskStatus.FAILED)

    _add_run(
        sf,
        task_a,
        input_tokens=1000,
        output_tokens=2000,
        reported_cost_usd=0.4,
    )
    _add_run(
        sf,
        task_a,
        input_tokens=0,
        output_tokens=0,
        reported_cost_usd=0.1,
    )
    # A run with no reported cost must count as 0, not break the sum.
    _add_run(sf, task_b, input_tokens=500, output_tokens=100, reported_cost_usd=None)

    body = routes_client.client.get("/stats").json()

    assert body["total_runs"] == 3
    assert body["input_tokens"] == 1500
    assert body["output_tokens"] == 2100
    assert body["total_cost_usd"] == 0.5


def test_stats_cost_rounds_float_noise(routes_client):
    sf = routes_client.session_factory
    project_id = _seed_project(sf)
    milestone_id = _add_milestone(sf, project_id, "M1", "Routes")
    task_id = _add_task(sf, milestone_id, TaskStatus.COMPLETED)

    _add_run(sf, task_id, reported_cost_usd=0.1)
    _add_run(sf, task_id, reported_cost_usd=0.2)

    body = routes_client.client.get("/stats").json()

    assert body["total_cost_usd"] == 0.3


def test_stats_groups_tasks_and_cost_by_milestone(routes_client):
    sf = routes_client.session_factory
    project_id = _seed_project(sf)
    m1 = _add_milestone(sf, project_id, "M6", "M6 - Token and Cost Tracking")
    m2 = _add_milestone(sf, project_id, "M7", "M7 - Dashboard")
    _add_milestone(sf, project_id, "M8", "M8 - Unused")  # no tasks: excluded

    m1_task = _add_task(sf, m1, TaskStatus.COMPLETED)
    _add_task(sf, m1, TaskStatus.FAILED)
    m2_task = _add_task(sf, m2, TaskStatus.COMPLETED)
    _add_task(sf, m2, TaskStatus.QUEUED)
    _add_task(sf, m2, TaskStatus.QUEUED)

    _add_run(sf, m1_task, reported_cost_usd=0.22)
    _add_run(sf, m2_task, reported_cost_usd=0.05)
    _add_run(sf, m2_task, reported_cost_usd=0.03)

    body = routes_client.client.get("/stats").json()

    assert body["total_tasks"] == 5
    assert body["total_cost_usd"] == 0.3
    assert body["milestones"] == [
        {
            "code": "M6",
            "title": "M6 - Token and Cost Tracking",
            "task_count": 2,
            "cost_usd": 0.22,
        },
        {
            "code": "M7",
            "title": "M7 - Dashboard",
            "task_count": 3,
            "cost_usd": 0.08,
        },
    ]


def test_stats_milestone_without_runs_has_zero_cost(routes_client):
    sf = routes_client.session_factory
    project_id = _seed_project(sf)
    milestone_id = _add_milestone(sf, project_id, "M1", "Routes")
    _add_task(sf, milestone_id, TaskStatus.QUEUED)

    body = routes_client.client.get("/stats").json()

    assert body["milestones"] == [
        {"code": "M1", "title": "Routes", "task_count": 1, "cost_usd": 0.0}
    ]


def test_stats_is_read_only(routes_client):
    sf = routes_client.session_factory
    project_id = _seed_project(sf)
    milestone_id = _add_milestone(sf, project_id, "M1", "Routes")
    task_id = _add_task(sf, milestone_id, TaskStatus.COMPLETED)
    _add_run(sf, task_id, input_tokens=10, output_tokens=5, reported_cost_usd=0.01)

    before = (
        _count_rows(sf, Project),
        _count_rows(sf, Milestone),
        _count_rows(sf, Task),
        _count_rows(sf, AgentRun),
    )

    for _ in range(2):
        assert routes_client.client.get("/stats").status_code == 200

    after = (
        _count_rows(sf, Project),
        _count_rows(sf, Milestone),
        _count_rows(sf, Task),
        _count_rows(sf, AgentRun),
    )
    assert after == before


def test_stats_rejects_write_methods(routes_client):
    response = routes_client.client.post("/stats")

    assert response.status_code == 405
