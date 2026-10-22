
from fastapi import FastAPI

app = FastAPI(
    title="AI Coding Agent Orchestrator",
    description=(
        "Manage AI coding tasks, Claude Code agents, "
        "Git worktrees, verification, and usage metrics."
    ),
    version="0.1.0",
)


@app.get("/")
def root() -> dict[str, str]:
    """Return basic application information."""
    return {
        "name": "AI Coding Agent Orchestrator",
        "status": "running",
        "version": "0.1.0",
    }


@app.get("/health")
def health_check() -> dict[str, str]:
    """Report whether the application is responding."""
    return {"status": "healthy"}
