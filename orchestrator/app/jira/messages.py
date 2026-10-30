# The comments the orchestrator posts on a Jira issue.
from orchestrator.app.db.models import Task


def outcome_message(task: Task) -> str:
    """Summarize a finished task: status, per-role runs, cost and checks."""
    lines = [f"Task {task.id} finished with status **{task.status.value}**.", ""]

    total_cost = 0.0
    for run in task.agent_runs:
        cost = run.reported_cost_usd or 0.0
        total_cost += cost
        lines.append(
            f"- {run.agent_role} ({run.model or '?'}/{run.effort or '?'}): "
            f"{run.status}, {run.input_tokens} in / {run.output_tokens} out "
            f"tokens, ${cost:.4f}"
        )

    if task.verification_results:
        lines.append("")
        lines.extend(
            f"- check `{result.check_name}`: {'passed' if result.passed else 'failed'}"
            for result in task.verification_results
        )

    if task.pull_request_url:
        lines.extend(["", f"Pull request: {task.pull_request_url}"])

    lines.extend(["", f"Total reported cost: ${total_cost:.4f}"])
    return "\n".join(lines)
