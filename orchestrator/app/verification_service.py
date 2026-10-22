from sqlalchemy.orm import Session

from orchestrator.app.models import VerificationResult


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
