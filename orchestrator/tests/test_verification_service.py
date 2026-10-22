from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from orchestrator.app.database import Base
from orchestrator.app.models import Milestone, Project, Task, TaskStatus


def test_record_verification_result_persists_result():
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
                title="Add verification service",
                description="",
                status=TaskStatus.QUEUED,
            )
            session.add(task)
            session.commit()

            task_id = task.id

            from orchestrator.app.verification_service import (
                record_verification_result,
            )

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
    finally:
        engine.dispose()
