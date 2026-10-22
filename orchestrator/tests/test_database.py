
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from orchestrator.app.database import Base
from orchestrator.app.models import (
    AgentRun,
    Milestone,
    Project,
    Task,
    TaskStatus,
    VerificationResult,
)


def test_create_project_task_and_agent_run(tmp_path) -> None:
    database_file = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{database_file.as_posix()}"
    )

    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    with TestSession() as session:
        project = Project(name="agent-orchestrator")
        session.add(project)
        session.flush()

        milestone = Milestone(
            project_id=project.id,
            code="M1",
            title="Project Foundation",
        )
        session.add(milestone)
        session.flush()

        task = Task(
            milestone_id=milestone.id,
            title="Implement database models",
            status=TaskStatus.QUEUED,
        )
        session.add(task)
        session.flush()

        run = AgentRun(
            task_id=task.id,
            agent_role="builder",
            status="starting",
        )
        session.add(run)

        verification = VerificationResult(
            task_id=task.id,
            check_name="database_test",
            passed=True,
            details="Database entities were persisted.",
        )
        session.add(verification)
        session.commit()

        saved_task = session.scalar(
            select(Task).where(Task.id == task.id)
        )

        assert saved_task is not None
        assert saved_task.status == TaskStatus.QUEUED
        assert len(saved_task.agent_runs) == 1
        assert len(saved_task.verification_results) == 1

    engine.dispose()
