# Plans a requirement and creates its issues in Jira, once per requirement.
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.app.core.config import Settings
from orchestrator.app.jira.client import JiraApi
from orchestrator.app.jira.creator import CreatedIssues, create_issues
from orchestrator.app.jira.models import Requirement
from orchestrator.app.planning.requirements import RequirementPlan, RequirementPlanner


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


def plan_requirement(
    session: Session,
    planner: RequirementPlanner,
    api: JiraApi | None,
    settings: Settings,
    title: str,
    text: str,
    reference: str | None = None,
    dry_run: bool = False,
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
            return RequirementResult(None, None, earlier.epic_key, existing=True)

    plan = planner.plan(title, text)

    if dry_run or api is None:
        return RequirementResult(plan, None, None)

    created = create_issues(api, settings, plan)
    session.add(
        Requirement(
            reference=reference,
            title=title[:250],
            text=text,
            epic_key=created.epic,
        )
    )
    session.commit()
    return RequirementResult(plan, created, created.epic)
