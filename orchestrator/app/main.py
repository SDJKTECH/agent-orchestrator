
from fastapi import FastAPI

from orchestrator.app.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description=(
        "Manage AI coding tasks, Claude Code agents, "
        "Git worktrees, verification, and usage metrics."
    ),
    version=settings.app_version,
)


@app.get("/")
def root() -> dict[str, str]:
    """Return basic application information."""
    return {
        "name": settings.app_name,
        "status": "running",
        "version": settings.app_version,
    }


@app.get("/health")
def health_check() -> dict[str, str]:
    """Report whether the application is responding."""
    return {"status": "healthy"}
