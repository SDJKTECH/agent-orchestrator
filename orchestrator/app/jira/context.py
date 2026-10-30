# Tells the agents where a task sits in its Jira hierarchy (Epic > Story > Task).
# Read from the task's JiraItem; a task without one, or without parents, gets nothing.
from orchestrator.app.db.models import Task
from orchestrator.app.jira.models import JiraItem

MAX_PARENT_DESCRIPTION_CHARS = 1500


def _ancestors(task: Task) -> list[JiraItem]:
    """The task's Epic and Story, outermost first (empty if it has none)."""
    return task.jira_item.ancestors() if task.jira_item is not None else []


def _name(item: JiraItem) -> str:
    """e.g. 'Story SG-2 "Add obstacles"'."""
    return f'{item.type or "Parent"} {item.key} "{item.summary}"'


def describe_hierarchy(task: Task) -> str:
    """Prompt text for the builder, tester and reviewer; empty without parents."""
    ancestors = _ancestors(task)

    if not ancestors:
        return ""

    parts = [
        "This task is one part of a larger piece of work. The parent items below "
        "are background only: implement just this task, not the whole parent.\n"
    ]

    for item in ancestors:
        description = item.description.strip()[:MAX_PARENT_DESCRIPTION_CHARS]
        parts.append(f"{_name(item)}\n{description or '(no description)'}\n")

    return "\n".join(parts)


def summarize_hierarchy(task: Task) -> str:
    """One line for the planner, e.g. 'Epic SG-1 "Game" > Story SG-2 "Walls"'."""
    return " > ".join(_name(item) for item in _ancestors(task))
