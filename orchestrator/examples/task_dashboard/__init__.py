"""Task dashboard: render a static HTML dashboard from a JSON task list."""

from orchestrator.examples.task_dashboard.model import (
    STATUSES,
    Task,
    count_by_status,
    load_tasks,
    percent_done,
)
from orchestrator.examples.task_dashboard.render import render_html

__all__ = [
    "STATUSES",
    "Task",
    "count_by_status",
    "load_tasks",
    "percent_done",
    "render_html",
]
