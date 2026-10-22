import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request

from orchestrator.app.config import get_settings
from orchestrator.app.database import create_tables, engine
from orchestrator.app.execution_manager import ExecutionManager
from orchestrator.app.logging_config import setup_logging
from orchestrator.app.migrations import migrate_usage_columns
from orchestrator.app.task_routes import router as task_router
from orchestrator.app.usage_routes import router as usage_router

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    create_tables()
    migrate_usage_columns(engine)
    logger.info("Application startup")

    manager = ExecutionManager(max_workers=get_settings().max_concurrent_agents)
    app.state.execution_manager = manager

    try:
        yield
    finally:
        logger.info("Application shutdown")
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
app.include_router(task_router)
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
