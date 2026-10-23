from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.app.agents.execution_manager import ExecutionManager
from orchestrator.app.api.control_routes import router as control_router
from orchestrator.app.api.intake_routes import router as intake_router
from orchestrator.app.api.project_routes import router as project_router
from orchestrator.app.api.stats_routes import router as stats_router
from orchestrator.app.api.task_routes import router as task_router
from orchestrator.app.api.verification_routes import router as verification_router
from orchestrator.app.db.database import Base, get_db
from orchestrator.app.db.models import Milestone, Project


@pytest.fixture
def routes_client():
    """A minimal app with every router, backed by an in-memory database."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    app = FastAPI()
    for router in (
        project_router,
        intake_router,
        task_router,
        control_router,
        stats_router,
        verification_router,
    ):
        app.include_router(router)

    manager = ExecutionManager(max_workers=1)
    app.state.execution_manager = manager

    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            session_factory=session_factory,
            app=app,
            manager=manager,
        )

    manager.shutdown()
    engine.dispose()


@pytest.fixture
def milestone_id(routes_client) -> int:
    with routes_client.session_factory() as session:
        project = Project(name="routes-project")
        session.add(project)
        session.flush()
        milestone = Milestone(project_id=project.id, code="M1", title="Routes")
        session.add(milestone)
        session.commit()
        return milestone.id
