import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from orchestrator.app.database import Base
from orchestrator.app.models import AgentRun, Milestone, Project, Task, TaskStatus
from orchestrator.app.usage_service import get_milestone_usage, get_task_usage


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        yield session

    engine.dispose()


@pytest.fixture
def sample_data(db_session):
    project = Project(name="Usage test project")
    db_session.add(project)
    db_session.flush()

    milestone = Milestone(
        project_id=project.id,
        code="M6",
        title="Usage tracking",
    )
    db_session.add(milestone)
    db_session.flush()

    task = Task(
        milestone_id=milestone.id,
        title="Track usage",
        description="",
        status=TaskStatus.COMPLETED,
    )
    db_session.add(task)
    db_session.flush()

    db_session.add_all(
        [
            AgentRun(
                task_id=task.id,
                model="sonnet",
                status="COMPLETED",
                input_tokens=100,
                output_tokens=50,
                cache_creation_input_tokens=20,
                cache_read_input_tokens=30,
                reported_cost_usd=0.25,
                estimated_cost=0.20,
            ),
            AgentRun(
                task_id=task.id,
                model="sonnet",
                status="FAILED",
                input_tokens=10,
                output_tokens=5,
                reported_cost_usd=None,
                estimated_cost=None,
            ),
        ]
    )
    db_session.commit()

    return {"task_id": task.id, "milestone_id": milestone.id}


def test_get_task_usage_aggregates_all_runs(db_session, sample_data):
    summary = get_task_usage(db_session, sample_data["task_id"])

    assert summary == {
        "task_count": 1,
        "run_count": 2,
        "input_tokens": 110,
        "output_tokens": 55,
        "cache_creation_input_tokens": 20,
        "cache_read_input_tokens": 30,
        "reported_cost_usd": pytest.approx(0.25),
        "estimated_cost": pytest.approx(0.20),
        "reported_cost_run_count": 1,
        "estimated_cost_run_count": 1,
    }


def test_get_milestone_usage_aggregates_tasks(db_session, sample_data):
    summary = get_milestone_usage(db_session, sample_data["milestone_id"])

    assert summary["task_count"] == 1
    assert summary["run_count"] == 2
    assert summary["input_tokens"] == 110
    assert summary["output_tokens"] == 55
    assert summary["reported_cost_usd"] == pytest.approx(0.25)
    assert summary["reported_cost_run_count"] == 1
    assert summary["estimated_cost_run_count"] == 1


def test_get_task_usage_raises_for_missing_task(db_session):
    with pytest.raises(ValueError, match="Task with ID 999 does not exist"):
        get_task_usage(db_session, 999)


def test_get_milestone_usage_raises_for_missing_milestone(db_session):
    with pytest.raises(ValueError, match="Milestone with ID 999 does not exist"):
        get_milestone_usage(db_session, 999)
