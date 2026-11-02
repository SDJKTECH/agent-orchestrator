# Creates a planned Epic, its Stories and their Subtasks in Jira, with "Blocks" links
# for the dependencies between Subtasks. If anything fails, what was made is deleted.
import logging
import threading
from dataclasses import dataclass, field

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import OrchestratorError, PlanningError
from orchestrator.app.jira.client import JiraApi
from orchestrator.app.planning.requirements import RequirementPlan

logger = logging.getLogger(__name__)

# Held while a plan is made in Jira, and by the poller while it imports. Without it
# the poller could pick up a Subtask before the links that block it exist.
creation_lock = threading.Lock()


@dataclass
class CreatedIssues:
    """The Jira keys made for a plan."""

    epic: str
    # One key per planned Story, in order.
    stories: list[str] = field(default_factory=list)
    # Planner ref -> Jira key of each Subtask.
    subtasks: dict[str, str] = field(default_factory=dict)


def create_issues(
    api: JiraApi, settings: Settings, plan: RequirementPlan
) -> CreatedIssues:
    """Make the plan's issues in Jira; undo it and raise PlanningError on failure."""
    project = settings.jira_project

    if not project:
        raise PlanningError("JIRA_PROJECT is not set, so there is nowhere to plan.")

    made: list[str] = []

    def create(issue_type: str, summary: str, text: str, parent: str | None) -> str:
        key = api.create_issue(project, issue_type, summary, text, parent)
        made.append(key)
        return key

    with creation_lock:
        try:
            created = CreatedIssues(
                epic=create(
                    settings.jira_epic_type,
                    plan.epic_summary,
                    plan.epic_description,
                    None,
                )
            )

            for story in plan.stories:
                story_key = create(
                    settings.jira_story_type,
                    story.summary,
                    story.description,
                    created.epic,
                )
                created.stories.append(story_key)

                for sub in story.subtasks:
                    created.subtasks[sub.ref] = create(
                        settings.jira_subtask_type,
                        sub.summary,
                        sub.description,
                        story_key,
                    )

            for sub in plan.subtasks():
                for ref in sub.depends_on:
                    api.link_blocks(created.subtasks[ref], created.subtasks[sub.ref])
        except OrchestratorError as exc:
            _undo(api, made)
            raise PlanningError(
                f"Jira rejected the plan, so nothing was kept: {exc}"
            ) from exc

    logger.info(
        "Requirement planned in Jira: epic=%s stories=%s subtasks=%s",
        created.epic,
        len(created.stories),
        len(created.subtasks),
    )
    return created


def _undo(api: JiraApi, keys: list[str]) -> None:
    """Delete the issues just made, newest first. A failure is logged and skipped."""
    for key in reversed(keys):
        try:
            api.delete_issue(key)
        except OrchestratorError as exc:
            logger.warning("Could not delete %s while undoing a plan: %s", key, exc)
