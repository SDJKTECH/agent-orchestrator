import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request

from orchestrator.app import task_routes
from orchestrator.app.config import get_settings
from orchestrator.app.control_routes import router as control_router
from orchestrator.app.database import SessionLocal, create_tables, engine
from orchestrator.app.execution_manager import ExecutionManager
from orchestrator.app.github_reporter import GhIssueReporter
from orchestrator.app.intake_poller import IntakePoller
from orchestrator.app.intake_routes import router as intake_router
from orchestrator.app.logging_config import setup_logging
from orchestrator.app.migrations import migrate_task_columns, migrate_usage_columns
from orchestrator.app.project_routes import router as project_router
from orchestrator.app.task_routes import router as task_router
from orchestrator.app.usage_routes import router as usage_router

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    create_tables()
    migrate_usage_columns(engine)
    migrate_task_columns(engine)
    logger.info("Application startup")

    current = get_settings()
    manager = ExecutionManager(max_workers=current.max_concurrent_agents)
    app.state.execution_manager = manager

    poller: IntakePoller | None = None

    if current.poll_interval_seconds > 0:
        if current.repository_path is None:
            logger.warning("Issue polling is enabled but repository_path is not set.")
        else:
            reporter = (
                GhIssueReporter(current.repository_path, current.github_repo)
                if current.comment_on_issues
                else None
            )
            poller = IntakePoller(
                settings=current,
                session_factory=SessionLocal,
                manager=manager,
                run_pipeline=lambda task_id: task_routes._run_pipeline_in_background(
                    task_id
                ),
                reporter=reporter,
            )
            poller.start()

    try:
        yield
    finally:
        logger.info("Application shutdown")
        if poller is not None:
            poller.stop()
        manager.shutdown(wait=True)


app = FastAPI(
    title=settings.app_name,
    description=(
        "Manage AI coding tasks, Claude Code agents, "
        "Git worktrees, verification, and usage metrics."
    ),
    version=settings.app_version,
    lifespan=lifespan,
)
app.include_router(project_router)
app.include_router(intake_router)
app.include_router(task_router)
app.include_router(control_router)
app.include_router(usage_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid4())
    start_time = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Request failed: request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )
        raise

    duration_ms = (time.perf_counter() - start_time) * 1000

    logger.info(
        "Request completed: request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )

    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "status": "running",
        "version": settings.app_version,
    }


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "healthy"}
