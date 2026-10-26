"""Render a self-contained HTML dashboard for a list of tasks."""

from __future__ import annotations

from html import escape

from orchestrator.examples.task_dashboard.model import (
    STATUSES,
    Task,
    count_by_status,
    percent_done,
)

_LABELS = {"todo": "To do", "in_progress": "In progress", "done": "Done"}

_CSS = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 52rem;
  color: #1f2933; padding: 0 1rem; }
.cards { display: flex; gap: 1rem; margin-bottom: 1.5rem; }
.card { flex: 1; border: 1px solid #d0d7de; border-radius: 8px; padding: 1rem;
  text-align: center; }
.card .count { font-size: 2rem; font-weight: 700; }
.bar { background: #e4e7eb; border-radius: 8px; height: 1.5rem; overflow: hidden; }
.bar > div { background: #2da44e; height: 100%; }
table { border-collapse: collapse; width: 100%; margin-top: 1.5rem; }
th, td { border-bottom: 1px solid #d0d7de; padding: 0.5rem; text-align: left; }
.status-todo { color: #57606a; }
.status-in_progress { color: #9a6700; }
.status-done { color: #1a7f37; }
"""


def _row(task: Task) -> str:
    assignee = escape(task.assignee) if task.assignee else "&mdash;"
    status = escape(task.status)
    label = escape(_LABELS.get(task.status, task.status))
    return (
        f"<tr><td>{escape(task.title)}</td>"
        f'<td class="status-{status}">{label}</td>'
        f"<td>{assignee}</td></tr>"
    )


def render_html(tasks: list[Task]) -> str:
    """Return one self-contained HTML page for the given tasks."""
    counts = count_by_status(tasks)
    percent = percent_done(tasks)

    cards = "\n".join(
        f'<div class="card status-{status}">'
        f'<div class="count">{counts[status]}</div>'
        f"<div>{escape(_LABELS[status])}</div></div>"
        for status in STATUSES
    )

    if tasks:
        rows = "\n".join(_row(task) for task in tasks)
    else:
        rows = '<tr><td colspan="3">No tasks.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Task Dashboard</title>
<style>{_CSS}</style>
</head>
<body>
<h1>Task Dashboard</h1>
<section class="cards">
{cards}
</section>
<section>
<p>Progress: {percent}% done</p>
<div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100"
 aria-valuenow="{percent}"><div style="width: {percent}%"></div></div>
</section>
<table>
<thead><tr><th>Title</th><th>Status</th><th>Assignee</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</body>
</html>
"""
