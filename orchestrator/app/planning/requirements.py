# Asks a read-only planner agent to break a requirement into an Epic, Stories and
# Subtasks, with the dependencies between the Subtasks.
# Unlike dependency planning, a failure here is an error: nothing is created from a
# plan nobody could read.
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.app.agents.agent_runner import (
    ProcessRunner,
    SubprocessRunner,
    build_claude_command,
    resolve_executable,
)
from orchestrator.app.agents.model_router import ModelRouter
from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import OrchestratorError, PlanningError
from orchestrator.app.planning.planner import result_text

logger = logging.getLogger(__name__)

PLANNER_TIMEOUT_SECONDS = 900
READ_ONLY_TOOLS = ["Read", "Grep", "Glob"]
MAX_SUMMARY_CHARS = 250
MAX_DESCRIPTION_CHARS = 4000
MAX_REQUIREMENT_CHARS = 12000


@dataclass
class PlannedSubtask:
    """One unit of work: built by the agents on its own branch."""

    ref: str
    summary: str
    description: str
    depends_on: list[str] = field(default_factory=list)


@dataclass
class PlannedStory:
    """A slice of the requirement; its Subtasks share one pull request."""

    summary: str
    description: str
    subtasks: list[PlannedSubtask] = field(default_factory=list)


@dataclass
class RequirementPlan:
    """The Epic for a requirement and the Stories and Subtasks under it."""

    epic_summary: str
    epic_description: str
    stories: list[PlannedStory] = field(default_factory=list)

    def subtasks(self) -> list[PlannedSubtask]:
        """Every Subtask of every Story, in order."""
        return [sub for story in self.stories for sub in story.subtasks]


def build_prompt(title: str, text: str, max_stories: int, max_subtasks: int) -> str:
    """The message that asks the planner agent to plan one requirement."""
    return (
        "Plan the following requirement as one Epic, Stories under it and Subtasks "
        f"under each Story. Use at most {max_stories} Stories and {max_subtasks} "
        "Subtasks per Story. Subtasks are what the builder, tester and reviewer "
        "agents work on, one at a time; a Story's Subtasks are merged into a "
        "single pull request, so a Story should be something that can be reviewed "
        "as a whole.\n\n"
        f"Requirement: {title}\n\n{text.strip()[:MAX_REQUIREMENT_CHARS]}\n\n"
        "Reply with only the JSON object described in your instructions."
    )


def _clean(value: object, limit: int) -> str:
    """A string from the reply with its whitespace tidied, cut to `limit`."""
    return value.strip()[:limit] if isinstance(value, str) else ""


def parse_plan(
    text: str, title: str, max_stories: int, max_subtasks: int
) -> RequirementPlan:
    """Read the planner's JSON reply into a plan, dropping what cannot be used.

    Raises PlanningError when no Story with a Subtask is left.
    """
    start, end = text.find("{"), text.rfind("}")

    if start < 0 or end <= start:
        raise PlanningError("The planner did not answer with a plan.")

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise PlanningError("The planner's plan is not valid JSON.") from exc

    if not isinstance(data, dict):
        raise PlanningError("The planner's plan is not a JSON object.")

    epic = data.get("epic") if isinstance(data.get("epic"), dict) else {}
    plan = RequirementPlan(
        epic_summary=_clean(epic.get("summary"), MAX_SUMMARY_CHARS) or title[:250],
        epic_description=_clean(epic.get("description"), MAX_DESCRIPTION_CHARS),
    )
    wanted: dict[str, list[str]] = {}
    used: set[str] = set()
    stories = data.get("stories")

    for raw_story in stories[:max_stories] if isinstance(stories, list) else []:
        if not isinstance(raw_story, dict):
            continue

        story = PlannedStory(
            summary=_clean(raw_story.get("summary"), MAX_SUMMARY_CHARS),
            description=_clean(raw_story.get("description"), MAX_DESCRIPTION_CHARS),
        )
        raw_subtasks = raw_story.get("subtasks")
        story_refs: dict[str, list[str]] = {}

        for raw in (
            raw_subtasks[:max_subtasks] if isinstance(raw_subtasks, list) else []
        ):
            if not isinstance(raw, dict):
                continue

            summary = _clean(raw.get("summary"), MAX_SUMMARY_CHARS)
            ref = _clean(raw.get("ref"), 40) or f"S{len(used) + 1}"

            if not summary or ref in used or ref in story_refs:
                continue

            depends = raw.get("depends_on")
            story_refs[ref] = (
                [d for d in depends if isinstance(d, str)]
                if isinstance(depends, list)
                else []
            )
            story.subtasks.append(
                PlannedSubtask(
                    ref,
                    summary,
                    _clean(raw.get("description"), MAX_DESCRIPTION_CHARS),
                )
            )

        if story.summary and story.subtasks:
            plan.stories.append(story)
            used.update(story_refs)
            wanted.update(story_refs)

    if not plan.stories:
        raise PlanningError("The plan has no Story with a Subtask.")

    kept = {sub.ref for sub in plan.subtasks()}
    for sub in plan.subtasks():
        sub.depends_on = [
            ref
            for ref in dict.fromkeys(wanted.get(sub.ref, []))
            if ref in kept and ref != sub.ref
        ]

    _remove_cycles(plan.subtasks())
    return plan


def _remove_cycles(subtasks: list[PlannedSubtask]) -> None:
    """Drop, in order, each dependency that would close a cycle."""
    accepted: dict[str, list[str]] = {sub.ref: [] for sub in subtasks}

    def reaches(start: str, target: str) -> bool:
        seen, stack = set(), [start]

        while stack:
            ref = stack.pop()

            if ref == target:
                return True

            if ref not in seen:
                seen.add(ref)
                stack.extend(accepted[ref])

        return False

    for sub in subtasks:
        for ref in sub.depends_on:
            if not reaches(ref, sub.ref):
                accepted[sub.ref].append(ref)
            else:
                logger.warning(
                    "Dependency dropped, it is a cycle: %s -> %s", sub.ref, ref
                )

        sub.depends_on = accepted[sub.ref]


class RequirementPlanner:
    """Runs the story-planner agent over one requirement."""

    def __init__(self, settings: Settings, runner: ProcessRunner | None = None) -> None:
        self.settings = settings
        self.runner = runner or SubprocessRunner()

    def plan(self, title: str, text: str) -> RequirementPlan:
        """Break a requirement into an Epic, Stories and Subtasks."""
        if self.settings.repository_path is None:
            raise PlanningError("No repository_path is configured.")

        repository = Path(self.settings.repository_path)
        max_stories = self.settings.requirement_max_stories
        max_subtasks = self.settings.requirement_max_subtasks

        try:
            decision = ModelRouter(self.settings).route(
                role="story-planner",
                definition=repository / ".claude" / "agents" / "story-planner.md",
            )
            command = build_claude_command(
                claude_command=resolve_executable(self.settings.claude_command),
                prompt=build_prompt(title, text, max_stories, max_subtasks),
                model=decision.model,
                effort=decision.effort,
                max_budget_usd=self.settings.max_task_cost_usd,
                agent="story-planner",
                permission_mode=self.settings.claude_permission_mode,
                allowed_tools=READ_ONLY_TOOLS,
            )
            result = self.runner.run(
                command, cwd=repository, timeout_seconds=PLANNER_TIMEOUT_SECONDS
            )
        except (OrchestratorError, ValueError) as exc:
            raise PlanningError(f"The planner could not run: {exc}") from exc

        if result.return_code != 0:
            raise PlanningError("The planner agent failed.")

        reply = result_text(result.stdout)
        logger.info("Requirement planner reply: %s", re.sub(r"\s+", " ", reply)[:600])
        return parse_plan(reply, title, max_stories, max_subtasks)
