# HTTP endpoint that imports open GitHub issues (or Jira Tasks) as queued tasks.
# Issues already imported are skipped, so calling it repeatedly is safe.
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from orchestrator.app.api.schemas import SyncResponse
from orchestrator.app.core.config import get_settings
from orchestrator.app.core.exceptions import IssueSourceError
from orchestrator.app.db.database import get_db
from orchestrator.app.db.models import Milestone
from orchestrator.app.github.intake import IssueSource, build_issue_source, sync_issues
from orchestrator.app.jira.importer import JiraItemSource, sync_jira_items
from orchestrator.app.jira.source import make_jira_source

router = APIRouter(tags=["intake"])


def get_issue_source() -> IssueSource | JiraItemSource:
    """Dependency that builds the configured issue source."""
    try:
        settings = get_settings()

        if settings.issue_source == "jira":
            return make_jira_source(settings)

        return build_issue_source(settings)
    except IssueSourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.post(
    "/milestones/{milestone_id}/sync",
    response_model=SyncResponse,
)
def sync_milestone_issues_endpoint(
    milestone_id: int,
    session: Session = Depends(get_db),
    source: IssueSource | JiraItemSource = Depends(get_issue_source),
) -> SyncResponse:
    """Import open issues as queued tasks, skipping ones already imported."""
    settings = get_settings()

    if settings.repository_path is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No repository_path is configured.",
        )

    if session.get(Milestone, milestone_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Milestone with ID {milestone_id} does not exist.",
        )

    try:
        worktrees_root = Path(settings.repository_path).parent / "agent-worktrees"

        if settings.issue_source == "jira":
            result = sync_jira_items(session, milestone_id, source, worktrees_root)
        else:
            result = sync_issues(
                session=session,
                milestone_id=milestone_id,
                source=source,
                worktrees_root=worktrees_root,
            )
    except IssueSourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return SyncResponse(
        imported=result.imported,
        skipped_issue_numbers=result.skipped,
        failed=result.failed,
    )
