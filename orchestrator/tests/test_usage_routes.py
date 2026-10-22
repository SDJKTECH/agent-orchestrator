import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from orchestrator.app.database import Base, get_db
from orchestrator.app.models import AgentRun, Milestone, Project, Task, TaskStatus
from orchestrator.app.usage_routes import router


@pytest.fixture
def client_and_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        app = FastAPI()
        app.include_router(router)

        def override_get_db():
            yield session

        app.dependency_overrides[get_db] = override_get_db

        with TestClient(app) as client:
            yield client, session

        app.dependency_overrides.clear()

    engine.dispose()


@pytest.fixture
def sample_data(client_and_session):
    _, session = client_and_session

    project = Project(name="Usage API project")
    session.add(project)
    session.flush()

    milestone = Milestone(
        project_id=project.id,
        code="M6",
        title="Usage tracking",
    )
    session.add(milestone)
    session.flush()

    task = Task(
        milestone_id=milestone.id,
        title="Track usage",
        description="",
        status=TaskStatus.COMPLETED,
    )
    session.add(task)
    session.flush()

    session.add_all(
        [
            AgentRun(
                task_id=task.id,
                agent_role="developer",
                model="sonnet",
                effort="medium",
                status="COMPLETED",
                input_tokens=100,
                output_tokens=50,
                cache_creation_input_tokens=20,
                cache_read_input_tokens=30,
                reported_cost_usd=0.25,
                estimated_cost=0.20,
                session_id="session-1",
            ),
            AgentRun(
                task_id=task.id,
                agent_role="reviewer",
                model="sonnet",
                effort="medium",
                status="FAILED",
                input_tokens=10,
                output_tokens=5,
            ),
        ]
    )
    session.commit()

    return {"task_id": task.id, "milestone_id": milestone.id}


def test_get_task_usage_endpoint(client_and_session, sample_data):
    client, _ = client_and_session

    response = client.get(f"/tasks/{sample_data['task_id']}/usage")

    assert response.status_code == 200
    data = response.json()
    assert data["task_count"] == 1
    assert data["run_count"] == 2
    assert data["input_tokens"] == 110
    assert data["output_tokens"] == 55
    assert data["cache_creation_input_tokens"] == 20
    assert data["cache_read_input_tokens"] == 30
    assert data["reported_cost_usd"] == pytest.approx(0.25)
    assert data["estimated_cost"] == pytest.approx(0.20)
    assert data["reported_cost_run_count"] == 1
    assert data["estimated_cost_run_count"] == 1


def test_get_task_runs_endpoint(client_and_session, sample_data):
    client, _ = client_and_session

    response = client.get(f"/tasks/{sample_data['task_id']}/runs")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["input_tokens"] == 100
    assert data[0]["session_id"] == "session-1"
    assert data[1]["status"] == "FAILED"


def test_get_milestone_usage_endpoint(client_and_session, sample_data):
    client, _ = client_and_session

    response = client.get(f"/milestones/{sample_data['milestone_id']}/usage")

    assert response.status_code == 200
    data = response.json()
    assert data["task_count"] == 1
    assert data["run_count"] == 2
    assert data["input_tokens"] == 110


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/tasks/999/usage", "Task with ID 999 does not exist."),
        ("/tasks/999/runs", "Task with ID 999 does not exist."),
        (
            "/milestones/999/usage",
            "Milestone with ID 999 does not exist.",
        ),
    ],
)
def test_usage_endpoints_return_404_for_missing_resources(
    client_and_session, path, message
):
    client, _ = client_and_session

    response = client.get(path)

    assert response.status_code == 404
    assert response.json()["detail"] == message
