"""Task model, JSON loading with validation, and summary statistics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

STATUSES: tuple[str, ...] = ("todo", "in_progress", "done")


@dataclass(frozen=True)
class Task:
    title: str
    status: str
    assignee: str | None = None


def _parse_task(index: int, item: object) -> Task:
    where = f"task #{index + 1}"
    if not isinstance(item, dict):
        raise ValueError(f"{where}: expected an object, got {type(item).__name__}")

    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"{where}: missing or empty 'title' (must be a string)")

    status = item.get("status")
    if status not in STATUSES:
        raise ValueError(
            f"{where} ({title!r}): unknown status {status!r}; "
            f"expected one of {', '.join(STATUSES)}"
        )

    assignee = item.get("assignee")
    if assignee is not None and not isinstance(assignee, str):
        raise ValueError(f"{where} ({title!r}): 'assignee' must be a string")

    return Task(title=title, status=status, assignee=assignee or None)


def load_tasks(path: str | Path) -> list[Task]:
    """Load and validate tasks from a JSON file containing a list of objects."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.strerror or exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError("invalid input: top-level JSON value must be a list of tasks")

    return [_parse_task(i, item) for i, item in enumerate(data)]


def count_by_status(tasks: list[Task]) -> dict[str, int]:
    """Count tasks per status; every known status is present."""
    counts = dict.fromkeys(STATUSES, 0)
    for task in tasks:
        counts[task.status] += 1
    return counts


def percent_done(tasks: list[Task]) -> int:
    """Percentage (rounded down to an int) of tasks that are done; 0 if empty."""
    if not tasks:
        return 0
    return count_by_status(tasks)["done"] * 100 // len(tasks)
