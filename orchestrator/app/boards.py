# Picks what mirrors a task's progress outside the orchestrator: the Jira tracker,
# or a GitHub Projects board. Neither package imports the other.
from orchestrator.app.core.config import Settings
from orchestrator.app.github.board_reporter import TaskBoard
from orchestrator.app.github.board_reporter import make_board as make_github_board
from orchestrator.app.jira.tracker import make_jira_tracker


def make_board(settings: Settings) -> TaskBoard | None:
    """The Jira tracker with ISSUE_SOURCE=jira, else the GitHub card mover (or None)."""
    if settings.issue_source == "jira":
        return make_jira_tracker(settings)

    return make_github_board(settings)
