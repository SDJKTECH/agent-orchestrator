# Reads the Jira issues waiting to be worked on and turns them into Issues.
# The Jira key is put in the task title so branches, commits and PRs carry it.
import logging

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import IssueSourceError
from orchestrator.app.github.intake import Issue
from orchestrator.app.jira.client import (
    JiraApi,
    adf_to_text,
    make_jira_api,
    quote_jql,
)

logger = logging.getLogger(__name__)


def build_jql(settings: Settings) -> str:
    """The query that finds the issues to pick up."""
    if settings.jira_jql:
        return settings.jira_jql

    if not settings.jira_project:
        raise IssueSourceError("Set JIRA_PROJECT (a project key) or JIRA_JQL.")

    return (
        f"project = {quote_jql(settings.jira_project)} "
        f"AND status = {quote_jql(settings.jira_trigger_status)} "
        "ORDER BY created ASC"
    )


def blockers(fields: dict) -> list[str]:
    """Keys of the issues this one "is blocked by"."""
    keys = []

    for link in fields.get("issuelinks") or []:
        other = link.get("inwardIssue") if isinstance(link, dict) else None
        kind = (link.get("type") or {}).get("name") if isinstance(link, dict) else None

        if kind == "Blocks" and isinstance(other, dict) and other.get("key"):
            keys.append(str(other["key"]))

    return keys


class JiraIssueSource:
    """Issues of a Jira query, as work items."""

    def __init__(self, api: JiraApi, jql: str, limit: int = 50) -> None:
        self.api = api
        self.jql = jql
        self.limit = limit

    def list_issues(self) -> list[Issue]:
        issues: list[Issue] = []

        for item in self.api.search(self.jql, self.limit):
            key = item.get("key")
            fields = item.get("fields") or {}
            summary = str(fields.get("summary") or "").strip()

            if not isinstance(key, str) or not summary:
                continue

            versions = fields.get("fixVersions") or []
            project = fields.get("project") or {}
            description = adf_to_text(fields.get("description")).strip()
            issues.append(
                Issue(
                    number=0,
                    title=f"{key}: {summary}",
                    body=(
                        f"Jira issue {key}: {self.api.browse_url(key)}\n\n{description}"
                    ).strip(),
                    labels=[str(label) for label in fields.get("labels") or []],
                    milestone=versions[0].get("name") if versions else None,
                    board_title=project.get("name")
                    if isinstance(project, dict)
                    else None,
                    jira_key=key,
                    blocked_by=blockers(fields),
                )
            )

        return issues


def make_jira_source(settings: Settings) -> JiraIssueSource:
    return JiraIssueSource(make_jira_api(settings), build_jql(settings))
