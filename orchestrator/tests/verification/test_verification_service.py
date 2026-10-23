import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from orchestrator.app.db.database import Base
from orchestrator.app.db.models import (
    Milestone,
    Project,
    Task,
    TaskStatus,
    VerificationResult,
)
from orchestrator.app.verification.service import record_verification_result


@pytest.fixture
def verification_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    try:
        with Session(engine) as session:
            project = Project(name="Verification test project")
            session.add(project)
            session.flush()

            milestone = Milestone(
                project_id=project.id,
                code="M7",
                title="Verification and Quality Gates",
            )
            session.add(milestone)
            session.flush()

            task = Task(
                milestone_id=milestone.id,
                title="Test verification",
                description="",
                status=TaskStatus.QUEUED,
            )
            session.add(task)
            session.commit()

            yield session, task.id
    finally:
        engine.dispose()


def test_record_verification_result_persists_result(verification_session):
    session, task_id = verification_session

    result = record_verification_result(
        session=session,
        task_id=task_id,
        check_name="unit_tests",
        passed=True,
        details="All unit tests passed.",
    )

    assert result.id is not None
    assert result.task_id == task_id
    assert result.check_name == "unit_tests"
    assert result.passed is True
    assert result.details == "All unit tests passed."
    assert result.created_at is not None


def test_record_verification_result_persists_failed_check(
    verification_session,
):
    session, task_id = verification_session

    result = record_verification_result(
        session=session,
        task_id=task_id,
        check_name="ruff",
        passed=False,
        details="Ruff reported linting errors.",
    )

    assert result.id is not None
    assert result.task_id == task_id
    assert result.check_name == "ruff"
    assert result.passed is False
    assert result.details == "Ruff reported linting errors."
    assert result.created_at is not None


def test_record_multiple_verification_results_for_same_task(
    verification_session,
):
    session, task_id = verification_session

    first = record_verification_result(
        session=session,
        task_id=task_id,
        check_name="unit_tests",
        passed=True,
        details="All unit tests passed.",
    )
    second = record_verification_result(
        session=session,
        task_id=task_id,
        check_name="ruff",
        passed=False,
        details="Linting errors found.",
    )

    assert first.id != second.id
    assert first.task_id == second.task_id == task_id
    assert first.check_name == "unit_tests"
    assert first.passed is True
    assert second.check_name == "ruff"
    assert second.passed is False

    results = session.query(VerificationResult).filter_by(task_id=task_id).all()
    assert len(results) == 2
