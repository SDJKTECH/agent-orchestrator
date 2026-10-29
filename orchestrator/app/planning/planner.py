# Asks a read-only planner agent which tasks need the code of other tasks.
# Any failure means "no dependencies": planning must never stop work from running.
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from orchestrator.app.agents.agent_runner import (
    ProcessRunner,
    SubprocessRunner,
    build_claude_command,
    resolve_executable,
)
from orchestrator.app.agents.model_router import ModelRouter
from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.db.models import Task

logger = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 1500
MAX_LOGGED_REPLY_CHARS = 600
MIN_NAMED_TITLE_CHARS = 12
PLANNER_TIMEOUT_SECONDS = 300
READ_ONLY_TOOLS = ["Read", "Grep", "Glob"]


@dataclass(frozen=True)
class Dependency:
    """One task needs another's code."""

    blocker: str
    reason: str


def result_text(stdout: str) -> str:
    """The agent's final message from Claude Code JSON output."""
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return stdout or ""

    if isinstance(payload, dict) and isinstance(payload.get("result"), str):
        return payload["result"]

    return stdout or ""


def task_label(task: Task) -> str:
    """How a task is named to the planner: its Jira key, else T<id>."""
    return task.jira_key or f"T{task.id}"


def build_prompt(tasks: list[Task]) -> str:
    """The message that asks the planner agent which tasks depend on which."""
    items = "\n\n".join(
        f"[{task_label(task)}] {task.title}\n"
        + (
            (task.description or "").strip()[:MAX_DESCRIPTION_CHARS]
            or "(no description)"
        )
        for task in tasks
    )
    return (
        "Decide which of these software tasks depend on each other. Task B depends "
        "on task A only when B needs code that A creates or changes (an API, a "
        "module, a file) and cannot be built correctly without it. Tasks that merely "
        "touch the same area, or can be built in any order, are independent. Be "
        "conservative: when unsure, treat them as independent. But a task whose "
        "text says it builds on, uses or extends another task's module, file or "
        "feature (for example by naming that task) does depend on it. Never "
        "output a cycle.\n\n"
        "Tasks:\n\n"
        + items
        + "\n\nReply with only a JSON object mapping each dependent task to what it "
        'needs, for example {"B": [{"blocked_by": "A", "reason": "uses A\'s '
        'parser"}]}. Use exactly the labels in square brackets. Use {} if no task '
        "depends on another."
    )


def parse_plan(text: str, labels: set[str]) -> dict[str, list[Dependency]]:
    """Read the planner's JSON reply; unknown labels and self-references are dropped."""
    start, end = text.find("{"), text.rfind("}")

    if start < 0 or end <= start:
        return {}

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}

    if not isinstance(data, dict):
        return {}

    plan: dict[str, list[Dependency]] = {}

    for blocked, entries in data.items():
        if blocked not in labels or not isinstance(entries, list):
            continue

        for entry in entries:
            blocker = entry.get("blocked_by") if isinstance(entry, dict) else entry
            reason = str(entry.get("reason", "")) if isinstance(entry, dict) else ""

            if isinstance(blocker, str) and blocker in labels and blocker != blocked:
                plan.setdefault(blocked, []).append(
                    Dependency(blocker, re.sub(r"\s+", " ", reason).strip()[:300])
                )

    return plan


def bare_title(task: Task) -> str:
    """The task's title without its Jira key prefix."""
    prefix = f"{task.jira_key}: " if task.jira_key else ""
    title = task.title or ""
    return title[len(prefix) :] if prefix and title.startswith(prefix) else title


def named_dependencies(tasks: list[Task]) -> dict[str, list[Dependency]]:
    """Dependencies stated in the task text itself.

    A task that quotes another task's title ("... added by the "Add X" task ...")
    needs that task; this does not rely on the planner agent's judgement.
    """
    plan: dict[str, list[Dependency]] = {}

    for task in tasks:
        text = (task.description or "").lower()

        for other in tasks:
            title = bare_title(other).strip().lower()

            if (
                other is task
                or len(title) < MIN_NAMED_TITLE_CHARS
                or not any(f"{quote}{title}{quote}" in text for quote in "\"'")
            ):
                continue

            plan.setdefault(task_label(task), []).append(
                Dependency(
                    task_label(other), f'the task text names "{bare_title(other)}"'
                )
            )

    return plan


def merge_plans(
    *plans: dict[str, list[Dependency]],
) -> dict[str, list[Dependency]]:
    """Combine plans; the first reason given for a dependency is kept."""
    merged: dict[str, list[Dependency]] = {}

    for plan in plans:
        for blocked, entries in plan.items():
            known = merged.setdefault(blocked, [])

            for entry in entries:
                if all(entry.blocker != other.blocker for other in known):
                    known.append(entry)

    return merged


class DependencyPlanner:
    """Runs the planner agent over a batch of tasks."""

    def __init__(self, settings: Settings, runner: ProcessRunner | None = None) -> None:
        self.settings = settings
        self.runner = runner or SubprocessRunner()

    def plan(self, tasks: list[Task]) -> dict[str, list[Dependency]]:
        """Dependencies for a batch of tasks: the agent's answer plus the named ones."""
        if len(tasks) < 2 or self.settings.repository_path is None:
            return {}

        repository = Path(self.settings.repository_path)
        named = named_dependencies(tasks)

        try:
            decision = ModelRouter(self.settings).route(
                role="planner",
                definition=repository / ".claude" / "agents" / "planner.md",
            )
            command = build_claude_command(
                claude_command=resolve_executable(self.settings.claude_command),
                prompt=build_prompt(tasks),
                model=decision.model,
                effort=decision.effort,
                max_budget_usd=self.settings.max_task_cost_usd,
                agent="planner",
                permission_mode=self.settings.claude_permission_mode,
                allowed_tools=READ_ONLY_TOOLS,
            )
            result = self.runner.run(
                command,
                cwd=repository,
                timeout_seconds=PLANNER_TIMEOUT_SECONDS,
            )
        except (OrchestratorError, ValueError) as exc:
            logger.warning(
                "Dependency planning failed; using named dependencies only: %s", exc
            )
            return named

        if result.return_code != 0:
            logger.warning("Dependency planner failed; using named dependencies only.")
            return named

        reply = result_text(result.stdout)
        logger.info(
            "Dependency planner reply: %s",
            re.sub(r"\s+", " ", reply)[:MAX_LOGGED_REPLY_CHARS],
        )
        plan = merge_plans(
            parse_plan(reply, {task_label(task) for task in tasks}), named
        )
        logger.info(
            "Dependency plan: %s", {key: len(value) for key, value in plan.items()}
        )
        return plan
