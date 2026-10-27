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
)
from orchestrator.app.agents.model_router import ModelRouter
from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import OrchestratorError
from orchestrator.app.db.models import Task

logger = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 1500
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
        "conservative: when unsure, treat them as independent. Never output a "
        "cycle.\n\n"
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


class DependencyPlanner:
    """Runs the planner agent over a batch of tasks."""

    def __init__(self, settings: Settings, runner: ProcessRunner | None = None) -> None:
        self.settings = settings
        self.runner = runner or SubprocessRunner()

    def plan(self, tasks: list[Task]) -> dict[str, list[Dependency]]:
        if len(tasks) < 2 or self.settings.repository_path is None:
            return {}

        repository = Path(self.settings.repository_path)

        try:
            decision = ModelRouter(self.settings).route(
                role="planner",
                definition=repository / ".claude" / "agents" / "planner.md",
            )
            command = build_claude_command(
                claude_command=self.settings.claude_command,
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
                "Dependency planning failed; tasks run independently: %s", exc
            )
            return {}

        if result.return_code != 0:
            logger.warning("Dependency planner failed; tasks run independently.")
            return {}

        plan = parse_plan(
            result_text(result.stdout), {task_label(task) for task in tasks}
        )
        logger.info(
            "Dependency plan: %s", {key: len(value) for key, value in plan.items()}
        )
        return plan
