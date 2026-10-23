# HTTP endpoint GET /verification/summary: a read-only count of how often
# each verification check has passed or failed.
from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from orchestrator.app.api.schemas import (
    VerificationCheckSummary,
    VerificationSummaryResponse,
)
from orchestrator.app.db.database import get_db
from orchestrator.app.db.models import VerificationResult

router = APIRouter(tags=["verification"])


@router.get("/verification/summary", response_model=VerificationSummaryResponse)
def get_verification_summary(
    session: Session = Depends(get_db),
) -> VerificationSummaryResponse:
    """Read-only pass and fail counts per verification check."""
    passed_count = func.coalesce(
        func.sum(case((VerificationResult.passed.is_(True), 1), else_=0)), 0
    )
    failed_count = func.coalesce(
        func.sum(case((VerificationResult.passed.is_(True), 0), else_=1)), 0
    )
    rows = session.execute(
        select(VerificationResult.check_name, passed_count, failed_count)
        .group_by(VerificationResult.check_name)
        .order_by(VerificationResult.check_name)
    ).all()

    checks = [
        VerificationCheckSummary(
            check_name=check_name, passed=int(passed), failed=int(failed)
        )
        for check_name, passed, failed in rows
    ]
    passed_total = sum(check.passed for check in checks)
    failed_total = sum(check.failed for check in checks)

    return VerificationSummaryResponse(
        total_results=passed_total + failed_total,
        passed=passed_total,
        failed=failed_total,
        checks=checks,
    )
