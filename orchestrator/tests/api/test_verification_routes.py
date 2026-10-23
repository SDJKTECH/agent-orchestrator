from sqlalchemy import func, select

from orchestrator.app.db.models import (
    Milestone,
    Project,
    Task,
    TaskStatus,
    VerificationResult,
)


def add_task(session, milestone_id: int) -> int:
    task = Task(
        milestone_id=milestone_id,
        title="Verification task",
        status=TaskStatus.COMPLETED,
    )
    session.add(task)
    session.flush()
    return task.id


def add_result(session, task_id: int, check_name: str, passed: bool) -> None:
    session.add(
        VerificationResult(task_id=task_id, check_name=check_name, passed=passed)
    )


def test_summary_on_empty_database_returns_zeros(routes_client) -> None:
    response = routes_client.client.get("/verification/summary")

    assert response.status_code == 200
    assert response.json() == {
        "total_results": 0,
        "passed": 0,
        "failed": 0,
        "checks": [],
    }


def test_summary_counts_passed_and_failed_per_check(
    routes_client, milestone_id
) -> None:
    with routes_client.session_factory() as session:
        first = add_task(session, milestone_id)
        second = add_task(session, milestone_id)
        third = add_task(session, milestone_id)
        # ruff_format: 2 passed, 1 failed
        add_result(session, first, "ruff_format", True)
        add_result(session, second, "ruff_format", True)
        add_result(session, third, "ruff_format", False)
        # ruff_check: 3 passed
        add_result(session, first, "ruff_check", True)
        add_result(session, second, "ruff_check", True)
        add_result(session, third, "ruff_check", True)
        # unit_tests: 3 passed
        add_result(session, first, "unit_tests", True)
        add_result(session, second, "unit_tests", True)
        add_result(session, third, "unit_tests", True)
        session.commit()

    data = routes_client.client.get("/verification/summary").json()

    assert data == {
        "total_results": 9,
        "passed": 8,
        "failed": 1,
        "checks": [
            {"check_name": "ruff_check", "passed": 3, "failed": 0},
            {"check_name": "ruff_format", "passed": 2, "failed": 1},
            {"check_name": "unit_tests", "passed": 3, "failed": 0},
        ],
    }


def test_summary_reports_check_with_only_failures(routes_client, milestone_id) -> None:
    with routes_client.session_factory() as session:
        task_id = add_task(session, milestone_id)
        add_result(session, task_id, "unit_tests", False)
        add_result(session, task_id, "unit_tests", False)
        session.commit()

    data = routes_client.client.get("/verification/summary").json()

    assert data["total_results"] == 2
    assert data["passed"] == 0
    assert data["failed"] == 2
    assert data["checks"] == [{"check_name": "unit_tests", "passed": 0, "failed": 2}]


def test_summary_sorts_checks_by_name(routes_client, milestone_id) -> None:
    with routes_client.session_factory() as session:
        task_id = add_task(session, milestone_id)
        # Insert out of alphabetical order, and make the first-inserted check
        # the one that sorts last, so insertion order cannot satisfy the test.
        add_result(session, task_id, "unit_tests", True)
        add_result(session, task_id, "ruff_format", False)
        add_result(session, task_id, "ruff_check", True)
        add_result(session, task_id, "mypy", True)
        session.commit()

    checks = routes_client.client.get("/verification/summary").json()["checks"]

    assert [check["check_name"] for check in checks] == [
        "mypy",
        "ruff_check",
        "ruff_format",
        "unit_tests",
    ]


def test_summary_counts_results_across_tasks_and_milestones(routes_client) -> None:
    with routes_client.session_factory() as session:
        project = Project(name="verification-project")
        session.add(project)
        session.flush()
        m1 = Milestone(project_id=project.id, code="M1", title="One")
        m2 = Milestone(project_id=project.id, code="M2", title="Two")
        session.add_all([m1, m2])
        session.flush()
        add_result(session, add_task(session, m1.id), "ruff_check", True)
        add_result(session, add_task(session, m2.id), "ruff_check", False)
        add_result(session, add_task(session, m2.id), "ruff_check", True)
        session.commit()

    data = routes_client.client.get("/verification/summary").json()

    assert data["total_results"] == 3
    assert data["passed"] == 2
    assert data["failed"] == 1
    assert data["checks"] == [{"check_name": "ruff_check", "passed": 2, "failed": 1}]


def test_summary_does_not_write_to_the_database(routes_client, milestone_id) -> None:
    with routes_client.session_factory() as session:
        task_id = add_task(session, milestone_id)
        add_result(session, task_id, "ruff_check", True)
        session.commit()

    def row_counts() -> tuple[int, int]:
        with routes_client.session_factory() as session:
            return tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (Task, VerificationResult)
            )

    before = row_counts()
    first = routes_client.client.get("/verification/summary")
    second = routes_client.client.get("/verification/summary")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert row_counts() == before
