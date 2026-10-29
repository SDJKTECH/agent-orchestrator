# Serves the dashboard page at GET /dashboard.
# Also serves its data at GET /dashboard/overview.
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from orchestrator.app.core.config import get_settings
from orchestrator.app.dashboard.schemas import DashboardOverview, RunDetail
from orchestrator.app.dashboard.service import build_overview, run_detail
from orchestrator.app.db.database import get_db
from orchestrator.app.github.client import detect_github_repo

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

PAGE = Path(__file__).with_name("index.html")


@lru_cache(maxsize=4)
def _detected_repo(repository_path: Path | None) -> str | None:
    """The GitHub repository (owner/name) of the local clone, if it can be found."""
    return detect_github_repo(repository_path)


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def dashboard_page() -> HTMLResponse:
    """The single-page monitoring dashboard."""
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


@router.get("/runs/{run_id}", response_model=RunDetail)
def dashboard_run(run_id: int, session: Session = Depends(get_db)) -> RunDetail:
    """One agent run in full, including the final message the agent reported."""
    detail = run_detail(session, run_id)

    if detail is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")

    return detail


@router.get("/overview", response_model=DashboardOverview)
def dashboard_overview(session: Session = Depends(get_db)) -> DashboardOverview:
    """A snapshot of every task, agent, stage, check and cost for the dashboard."""
    settings = get_settings()
    repo = settings.github_repo or _detected_repo(settings.repository_path)

    return build_overview(
        session,
        repo=repo,
        max_concurrent_agents=settings.max_concurrent_agents,
        polling_enabled=settings.poll_interval_seconds > 0,
    )
