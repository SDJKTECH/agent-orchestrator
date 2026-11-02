# HTTP endpoint that plans a requirement: the planner agent breaks it into an Epic,
# Stories and Subtasks, which are created in Jira for the agents to pick up.
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from orchestrator.app.core.config import get_settings
from orchestrator.app.core.exceptions import IssueSourceError, PlanningError
from orchestrator.app.db.database import get_db
from orchestrator.app.jira.client import JiraApi, make_jira_api
from orchestrator.app.planning.requirement_service import plan_requirement
from orchestrator.app.planning.requirements import RequirementPlanner

router = APIRouter(tags=["requirements"])


class RequirementCreate(BaseModel):
    """A requirement to plan."""

    title: str = Field(min_length=1, max_length=250)
    description: str = ""
    # Names the requirement (e.g. "github#12"); planning it again creates nothing.
    reference: str | None = Field(default=None, max_length=200)
    # Show the plan without creating anything in Jira.
    dry_run: bool = False


class PlannedSubtaskResponse(BaseModel):
    """A planned Subtask; `key` is set once it exists in Jira."""

    ref: str
    key: str | None
    summary: str
    depends_on: list[str]


class PlannedStoryResponse(BaseModel):
    """A planned Story and its Subtasks."""

    key: str | None
    summary: str
    subtasks: list[PlannedSubtaskResponse]


class RequirementResponse(BaseModel):
    """The plan for a requirement and the Jira keys made for it."""

    epic_key: str | None
    epic_summary: str | None
    created: bool
    already_planned: bool
    assumptions: list[str]
    stories: list[PlannedStoryResponse]


def get_planner() -> RequirementPlanner:
    """Dependency that builds the requirement planner."""
    return RequirementPlanner(get_settings())


def get_jira_api() -> JiraApi:
    """Dependency that builds the Jira client."""
    try:
        return make_jira_api(get_settings())
    except IssueSourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/requirements", response_model=RequirementResponse)
def plan_requirement_endpoint(
    body: RequirementCreate,
    session: Session = Depends(get_db),
    planner: RequirementPlanner = Depends(get_planner),
    api: JiraApi = Depends(get_jira_api),
) -> RequirementResponse:
    """Plan a requirement into Jira issues (or just show the plan with dry_run)."""
    try:
        result = plan_requirement(
            session,
            planner,
            api,
            get_settings(),
            body.title,
            body.description,
            body.reference,
            body.dry_run,
        )
    except PlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    plan, created = result.plan, result.created
    stories = []

    for index, story in enumerate(plan.stories if plan else []):
        stories.append(
            PlannedStoryResponse(
                key=created.stories[index] if created else None,
                summary=story.summary,
                subtasks=[
                    PlannedSubtaskResponse(
                        ref=sub.ref,
                        key=created.subtasks[sub.ref] if created else None,
                        summary=sub.summary,
                        depends_on=sub.depends_on,
                    )
                    for sub in story.subtasks
                ],
            )
        )

    return RequirementResponse(
        epic_key=result.epic_key,
        epic_summary=plan.epic_summary if plan else None,
        created=created is not None,
        already_planned=result.existing,
        assumptions=plan.assumptions if plan else [],
        stories=stories,
    )
