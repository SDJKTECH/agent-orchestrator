from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from orchestrator.app.api.schemas import SyncResponse
from orchestrator.app.core.config import get_settings
from orchestrator.app.core.exceptions import IssueSourceError
from orchestrator.app.db.database import get_db
from orchestrator.app.db.models import Milestone
from orchestrator.app.github.intake import IssueSource, build_issue_source, sync_issues

router = APIRouter(tags=["intake"])


def get_issue_source() -> IssueSource:
    """Dependency that builds the configured issue source."""
    try:
        return build_issue_source(get_settings())
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
    source: IssueSource = Depends(get_issue_source),
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
        result = sync_issues(
            session=session,
            milestone_id=milestone_id,
            source=source,
            worktrees_root=Path(settings.repository_path).parent / "agent-worktrees",
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
