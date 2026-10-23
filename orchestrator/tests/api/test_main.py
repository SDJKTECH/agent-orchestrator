import subprocess
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from orchestrator.app.agents.agent_runner import AgentExecutionResult
from orchestrator.app.core.config import get_settings
from orchestrator.app.core.exceptions import AgentExecutionError
from orchestrator.app.db.database import Base, get_db
from orchestrator.app.db.models import Milestone, Project, Task, TaskStatus
from orchestrator.app.main import app


@pytest.fixture
def api_environment(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()

    subprocess.run(
        ["git", "init", "-b", "main", str(repository)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "user.email",
            "test@example.com",
        ],
        check=True,
    )

    (repository / "README.md").write_text(
        "Test repository\n",
        encoding="utf-8",
    )

    agent_definition = repository / ".claude" / "agents" / "builder.md"
    agent_definition.parent.mkdir(parents=True)
    agent_definition.write_text(
        "---\n"
        "name: builder\n"
        "description: Test builder agent\n"
        "---\n\n"
        "Implement the assigned test task.\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["git", "-C", str(repository), "add", "README.md", ".claude"],
        check=True,
    )

    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )

    engine = create_engine(f"sqlite:///{(tmp_path / 'api-test.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    with TestSession() as session:
        project = Project(name="api-test-project", local_path=str(repository))
        session.add(project)
        session.flush()

        milestone = Milestone(
            project_id=project.id,
            code="M3",
            title="Task lifecycle",
        )
        session.add(milestone)
        session.commit()
        milestone_id = milestone.id

    def override_get_db():
        with TestSession() as session:
            yield session

    original_settings = get_settings()

    get_settings.cache_clear()
    app.dependency_overrides[get_db] = override_get_db

    import orchestrator.app.api.task_routes as task_routes
    from orchestrator.app.core.config import Settings

    test_settings = Settings(
        app_name=original_settings.app_name,
        app_version=original_settings.app_version,
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'api-test.db').as_posix()}",
        log_level="INFO",
        repository_path=repository,
    )

    original_route_settings = task_routes.get_settings
    original_session_local = task_routes.SessionLocal

    task_routes.get_settings = lambda: test_settings
    task_routes.SessionLocal = TestSession

    try:
        with TestClient(app) as client:
            yield (
                client,
                TestSession,
                milestone_id,
                repository,
                tmp_path / "agent-worktrees",
            )
    finally:
        app.dependency_overrides.clear()
        task_routes.get_settings = original_route_settings
        task_routes.SessionLocal = original_session_local
        get_settings.cache_clear()
        engine.dispose()


def test_root_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_create_task_endpoint(api_environment) -> None:
    client, test_session, milestone_id, repository, worktrees_root = api_environment

    response = client.post(
        "/tasks",
        json={
            "milestone_id": milestone_id,
            "title": "Create task API",
            "description": "Verify the API endpoint.",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Create task API"
    assert data["status"] == TaskStatus.QUEUED.value
    assert data["branch_name"] == f"agent/task-{data['id']}"
    assert data["worktree_path"] == str(worktrees_root / f"task-{data['id']}")
    assert (worktrees_root / f"task-{data['id']}" / "README.md").exists()

    with test_session() as session:
        saved_task = session.scalar(select(Task).where(Task.id == data["id"]))
        assert saved_task is not None
        assert saved_task.status == TaskStatus.QUEUED

    assert repository.exists()


def test_create_task_rejects_invalid_payload() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json={"milestone_id": 0, "title": "", "description": ""},
        )

    assert response.status_code == 422


def test_create_task_returns_bad_request_for_unknown_milestone(
    api_environment,
) -> None:
    client, _, _, _, _ = api_environment

    response = client.post(
        "/tasks",
        json={"milestone_id": 99999, "title": "Unknown milestone"},
    )

    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_create_task_returns_service_unavailable_without_repository_setting(
    monkeypatch,
) -> None:
    from orchestrator.app.api import task_routes
    from orchestrator.app.core.config import Settings

    test_settings = Settings(repository_path=None)
    monkeypatch.setattr(task_routes, "get_settings", lambda: test_settings)

    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json={"milestone_id": 1, "title": "No repository"},
        )

    assert response.status_code == 503
    assert "repository_path" in response.json()["detail"]


def test_run_task_endpoint_success(api_environment, monkeypatch) -> None:
    from orchestrator.app.agents.agent_service import AgentService
    from orchestrator.app.api import task_routes

    client, test_session, milestone_id, _, _ = api_environment

    create_response = client.post(
        "/tasks",
        json={
            "milestone_id": milestone_id,
            "title": "Test agent execution",
            "description": "Run the agent through the API.",
        },
    )
    assert create_response.status_code == 201

    task_data = create_response.json()

    fake_runner = MagicMock()
    fake_runner.run.return_value = AgentExecutionResult(
        return_code=0,
        stdout="Task completed",
        stderr="",
        process_id=None,
    )
    fake_verification_runner = MagicMock()
    fake_verification_runner.run_checks.return_value = [
        SimpleNamespace(
            check_name="unit_tests",
            passed=True,
            details="All tests passed",
        ),
        SimpleNamespace(
            check_name="ruff_check",
            passed=True,
            details="All checks passed",
        ),
        SimpleNamespace(
            check_name="ruff_format",
            passed=True,
            details="Formatting is correct",
        ),
    ]

    monkeypatch.setattr(
        task_routes,
        "AgentService",
        lambda db: AgentService(
            db=db,
            runner=fake_runner,
            verification_runner=fake_verification_runner,
        ),
    )

    task_completed = Event()
    original_background_runner = task_routes._run_task_in_background

    def tracked_background_runner(task_id: int) -> None:
        try:
            original_background_runner(task_id)
        finally:
            task_completed.set()

    monkeypatch.setattr(
        task_routes,
        "_run_task_in_background",
        tracked_background_runner,
    )

    response = client.post(f"/tasks/{task_data['id']}/run")

    assert response.status_code == 202
    data = response.json()
    assert data["task_id"] == task_data["id"]
    assert data["status"] == "QUEUED"
    assert data["message"] == "Agent execution has been scheduled."

    assert task_completed.wait(timeout=5), "Background task execution did not finish."
    fake_runner.run.assert_called_once()

    with test_session() as session:
        saved_task = session.get(Task, task_data["id"])
        assert saved_task is not None
        assert saved_task.status == TaskStatus.COMPLETED


def test_run_task_endpoint_returns_bad_request_for_unknown_task(
    api_environment,
) -> None:
    client, _, _, _, _ = api_environment

    response = client.post("/tasks/999999/run")

    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_run_task_endpoint_returns_accepted_on_agent_execution_failure(
    api_environment,
    monkeypatch,
) -> None:
    from orchestrator.app.api import task_routes

    client, _, milestone_id, _, _ = api_environment

    create_response = client.post(
        "/tasks",
        json={
            "milestone_id": milestone_id,
            "title": "Test failed agent",
            "description": "Simulate an agent execution error.",
        },
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["id"]

    fake_service = MagicMock()
    fake_service.run_task.side_effect = AgentExecutionError("Simulated failure")

    monkeypatch.setattr(
        task_routes,
        "AgentService",
        lambda db: fake_service,
    )

    response = client.post(f"/tasks/{task_id}/run")

    assert response.status_code == 202
    data = response.json()
    assert data["task_id"] == task_id
    assert data["status"] == "QUEUED"

    fake_service.run_task.assert_called_once_with(task_id)
