# Reads the Jira issues waiting to be worked on and turns them into Issues.
# The Jira key is put in the task title so branches, commits and PRs carry it.
import logging
from dataclasses import dataclass, field

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import IssueSourceError, OrchestratorError
from orchestrator.app.jira.client import (
    JiraApi,
    adf_to_text,
    make_jira_api,
    quote_jql,
)

logger = logging.getLogger(__name__)

# Epic > Story > Task needs two parents; one more level covers Sub-tasks.
MAX_ANCESTOR_DEPTH = 3
DEFAULT_SKIP_TYPES = frozenset({"epic", "story"})


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


def parse_skip_types(text: str | None) -> frozenset[str]:
    """Issue type names from a comma-separated setting, compared case-insensitively."""
    return frozenset(
        part.strip().casefold() for part in (text or "").split(",") if part.strip()
    )


@dataclass(frozen=True)
class JiraRecord:
    """One Jira issue as read from Jira: its type, text and parent."""

    key: str
    type: str = ""
    summary: str = ""
    description: str = ""
    status: str | None = None
    parent_key: str | None = None
    # How many Subtasks the issue has; an issue with some is a container.
    subtask_count: int = 0


@dataclass(frozen=True)
class JiraWorkItem:
    """A Jira issue to work on, with the Story and Epic above it (outermost first)."""

    record: JiraRecord
    ancestors: list[JiraRecord] = field(default_factory=list)
    url: str = ""
    labels: list[str] = field(default_factory=list)
    fix_version: str | None = None
    project_name: str | None = None
    blocked_by: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.record.key}: {self.record.summary}"

    @property
    def body(self) -> str:
        return (
            f"Jira issue {self.record.key}: {self.url}\n\n{self.record.description}"
        ).strip()


def read_record(item: dict) -> JiraRecord | None:
    """A JiraRecord from a Jira issue payload, or None if it has no key or summary."""
    key = item.get("key")
    fields = item.get("fields") or {}
    summary = str(fields.get("summary") or "").strip()

    if not isinstance(key, str) or not summary:
        return None

    kind = fields.get("issuetype") or {}
    status = fields.get("status") or {}
    parent = fields.get("parent") or {}
    subtasks = fields.get("subtasks")
    return JiraRecord(
        key=key,
        type=str(kind.get("name") or "") if isinstance(kind, dict) else "",
        summary=summary,
        description=adf_to_text(fields.get("description")).strip(),
        status=status.get("name") if isinstance(status, dict) else None,
        parent_key=parent.get("key") if isinstance(parent, dict) else None,
        subtask_count=len(subtasks) if isinstance(subtasks, list) else 0,
    )


class JiraIssueSource:
    """Issues of a Jira query, as work items."""

    def __init__(
        self,
        api: JiraApi,
        jql: str,
        limit: int = 50,
        skip_types: frozenset[str] = DEFAULT_SKIP_TYPES,
        skip_with_subtasks: bool = True,
    ) -> None:
        self.api = api
        self.jql = jql
        self.limit = limit
        self.skip_types = skip_types
        self.skip_with_subtasks = skip_with_subtasks

    def list_items(self) -> list[JiraWorkItem]:
        """Search Jira with the configured JQL; return the matches with their parents.

        Container types (Epic, Story by default) are left out: they are context for
        their Tasks, not work. So is any issue that has Subtasks, whatever its type is
        called, which keeps a misspelt JIRA_SKIP_TYPES from building a Story. A parent
        is read from Jira once per call.
        """
        found: dict[str, JiraRecord | None] = {}
        items: list[JiraWorkItem] = []

        for payload in self.api.search(self.jql, self.limit):
            record = read_record(payload)

            if record is None:
                continue

            if record.type.casefold() in self.skip_types:
                logger.debug("Skipping %s: a %s is not built.", record.key, record.type)
                continue

            if self.skip_with_subtasks and record.subtask_count:
                logger.debug(
                    "Skipping %s: its %d Subtask(s) are built instead.",
                    record.key,
                    record.subtask_count,
                )
                continue

            fields = payload.get("fields") or {}
            versions = fields.get("fixVersions") or []
            project = fields.get("project") or {}
            items.append(
                JiraWorkItem(
                    record=record,
                    ancestors=self._ancestors(record, found),
                    url=self.api.browse_url(record.key),
                    labels=[str(label) for label in fields.get("labels") or []],
                    fix_version=versions[0].get("name") if versions else None,
                    project_name=project.get("name")
                    if isinstance(project, dict)
                    else None,
                    blocked_by=blockers(fields),
                )
            )

        return items

    def _ancestors(
        self, record: JiraRecord, found: dict[str, JiraRecord | None]
    ) -> list[JiraRecord]:
        """The parent chain of `record`, outermost first (Epic, then Story).

        Stops at the top, at a loop, or after MAX_ANCESTOR_DEPTH levels. A parent
        Jira cannot show (deleted, no permission) ends the chain.
        """
        chain: list[JiraRecord] = []
        seen = {record.key}
        key = record.parent_key

        while key and key not in seen and len(chain) < MAX_ANCESTOR_DEPTH:
            if key not in found:
                payload = self.api.issue(key)
                found[key] = read_record(payload) if payload else None

            parent = found[key]

            if parent is None:
                break

            chain.append(parent)
            seen.add(key)
            key = parent.parent_key

        chain.reverse()
        return chain


def make_jira_source(settings: Settings) -> JiraIssueSource:
    """Build the Jira issue source from the settings."""
    return JiraIssueSource(
        make_jira_api(settings),
        build_jql(settings),
        skip_types=parse_skip_types(settings.jira_skip_types),
        skip_with_subtasks=settings.jira_skip_with_subtasks,
    )


def check_skip_types(settings: Settings, api: JiraApi | None = None) -> None:
    """Say what is skipped, and warn about a skip type that names no Jira issue type.

    Meant for start-up. It never raises: if Jira cannot be asked, it only logs.
    """
    skip = parse_skip_types(settings.jira_skip_types)
    logger.info(
        "Jira issues not built: types %s%s.",
        ", ".join(sorted(skip)) or "(none)",
        " and any issue that has Subtasks" if settings.jira_skip_with_subtasks else "",
    )

    try:
        known = (api or make_jira_api(settings)).issue_type_names()
    except OrchestratorError as exc:
        logger.info("Could not check JIRA_SKIP_TYPES against Jira: %s", exc)
        return

    unknown = sorted(skip - {name.casefold() for name in known})

    if known and unknown:
        logger.warning(
            "JIRA_SKIP_TYPES names no Jira issue type: %s. Check the spelling; "
            "Jira has: %s.",
            ", ".join(unknown),
            ", ".join(sorted(known)),
        )
