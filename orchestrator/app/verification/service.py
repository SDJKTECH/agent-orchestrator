# Stores one verification check result in the database.
# The dashboard and the fix loop read these records.
from sqlalchemy.orm import Session

from orchestrator.app.db.models import VerificationResult


def record_verification_result(
    session: Session,
    task_id: int,
    check_name: str,
    passed: bool,
    details: str = "",
) -> VerificationResult:
    result = VerificationResult(
        task_id=task_id,
        check_name=check_name,
        passed=passed,
        details=details,
    )
    session.add(result)
    session.commit()
    session.refresh(result)
    return result
