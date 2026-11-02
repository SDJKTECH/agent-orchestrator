# Plans a requirement and creates its issues in Jira, once per requirement.
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.jira.client import JiraApi
from orchestrator.app.jira.creator import CreatedIssues, create_issues
from orchestrator.app.jira.models import Requirement
from orchestrator.app.planning.requirements import RequirementPlan, RequirementPlanner

logger = logging.getLogger(__name__)


@dataclass
class RequirementResult:
    """What planning a requirement produced.

    `created` is None for a dry run, and for a requirement that was planned before:
    then only `epic_key` (the earlier Epic) is known and `plan` is None.
    """

    plan: RequirementPlan | None
    created: CreatedIssues | None
    epic_key: str | None
    existing: bool = False
    # Every Epic made (or made earlier); `epic_key` is the first.
    epic_keys: list[str] = field(default_factory=list)


MAX_ORIGINAL_CHARS = 8000


def _keep_original(
    api: JiraApi, epic: str, title: str, text: str, reference: str | None
) -> None:
    """Comment the requirement as written on the Epic, so the plan can be checked.

    The agents see only a short Epic description; this is for people. A failure
    to comment does not undo the plan.
    """
    source = f" ({reference})" if reference else ""
    body = f"Requirement{source} as it was written:\n\n{title}\n\n{text.strip()}"

    try:
        api.comment(epic, body[:MAX_ORIGINAL_CHARS])
    except OrchestratorError as exc:
        logger.warning("Could not keep the original requirement on %s: %s", epic, exc)


def plan_requirement(
    session: Session,
    planner: RequirementPlanner,
    api: JiraApi | None,
    settings: Settings,
    title: str,
    text: str,
    reference: str | None = None,
    dry_run: bool = False,
    structured: bool = False,
) -> RequirementResult:
    """Plan a requirement and (unless `dry_run`) create its Jira issues.

    A `reference` (for example "github#12") names the requirement: planning the
    same reference again creates nothing and returns the Epic made the first time.
    """
    if reference:
        earlier = session.scalar(
            select(Requirement).where(Requirement.reference == reference)
        )

        if earlier is not None:
            keys = [key for key in earlier.epic_key.split(",") if key]
            return RequirementResult(None, None, keys[0] if keys else None, True, keys)

    plan = planner.plan(title, text, structured)

    if dry_run or api is None:
        return RequirementResult(plan, None, None)

    created = create_issues(api, settings, plan)
    for epic in created.epics:
        _keep_original(api, epic, title, text, reference)
    session.add(
        Requirement(
            reference=reference,
            title=title[:250],
            text=text,
            epic_key=",".join(created.epics),
        )
    )
    session.commit()
    return RequirementResult(plan, created, created.epic, False, created.epics)
